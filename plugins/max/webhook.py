"""MAX Webhook validation with a durable SQLite inbox."""

from __future__ import annotations

import asyncio
import hmac
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from .polling_state import is_control_update, max_update_event_key, migrate_event_keys


@dataclass(frozen=True)
class WebhookResult:
    status_code: int
    accepted: bool
    duplicate: bool = False
    durable: bool = False
    queued: bool = False


def _header(headers: Mapping[str, str], name: str) -> str:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return ""


def _dedup_key(update: Mapping[str, Any]) -> str:
    return max_update_event_key(update)


class MaxWebhookReceiver:
    """Validate, durably enqueue and deduplicate MAX updates.

    The SQLite commit is the ACK gate. An in-memory queue is only a wake-up
    optimization; if the process restarts or the queue is full, pending rows
    remain available for the worker and a retry remains harmless.
    """

    HEADER_NAME = "X-Max-Bot-Api-Secret"

    def __init__(
        self,
        secret: str,
        *,
        max_queue_size: int = 256,
        inbox_path: str | Path | None = None,
        max_seen: int = 4096,
    ) -> None:
        if not secret:
            raise ValueError("MAX Webhook secret must not be empty")
        self._secret = secret
        self._max_seen = max_seen
        self.queue: asyncio.Queue[Mapping[str, Any]] = asyncio.Queue(maxsize=max_queue_size)
        self._queued_keys: set[str] = set()
        self._lock = threading.Lock()
        self._conn = self._open_db(inbox_path)
        self._initialize_db()

    @staticmethod
    def _open_db(inbox_path: str | Path | None) -> sqlite3.Connection:
        if inbox_path is None:
            return sqlite3.connect(":memory:", check_same_thread=False)
        path = Path(inbox_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(path), check_same_thread=False)

    def _initialize_db(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS max_webhook_inbox (
                    event_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    processed_at REAL,
                    started_at REAL,
                    last_error TEXT
                )
                """
            )
            columns = {
                str(row[1])
                for row in self._conn.execute("PRAGMA table_info(max_webhook_inbox)").fetchall()
            }
            if "started_at" not in columns:
                self._conn.execute("ALTER TABLE max_webhook_inbox ADD COLUMN started_at REAL")
            if "last_error" not in columns:
                self._conn.execute("ALTER TABLE max_webhook_inbox ADD COLUMN last_error TEXT")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS max_webhook_inbox_status_idx "
                "ON max_webhook_inbox(status, created_at)"
            )
            self._conn.commit()
            migrate_event_keys(self._conn, "max_webhook_inbox")

    async def receive(
        self,
        headers: Mapping[str, str],
        update: Mapping[str, Any],
    ) -> WebhookResult:
        supplied = _header(headers, self.HEADER_NAME)
        if not hmac.compare_digest(str(supplied), self._secret):
            return WebhookResult(status_code=403, accepted=False)

        key = _dedup_key(update)
        payload = json.dumps(update, ensure_ascii=False, separators=(",", ":"))
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM max_webhook_inbox WHERE event_key = ?", (key,)
            ).fetchone()
            is_new = row is None
            if is_new:
                self._conn.execute(
                    "INSERT INTO max_webhook_inbox(event_key, payload, status, created_at) "
                    "VALUES (?, ?, 'pending', ?)",
                    (key, payload, now),
                )
                self._conn.commit()
            elif row[0] in {"processed", "dispatching", "accepted", "processing", "failed", "unknown"}:
                return WebhookResult(status_code=200, accepted=False, duplicate=True, durable=True)

        queued = key in self._queued_keys
        if not queued:
            try:
                self.queue.put_nowait(update)
                self._queued_keys.add(key)
                queued = True
            except asyncio.QueueFull:
                # The durable row is enough to acknowledge safely. The worker
                # will discover it through ``pending_updates`` after the queue
                # drains.
                queued = False
        return WebhookResult(
            status_code=200,
            accepted=True,
            duplicate=not is_new,
            durable=True,
            queued=queued,
        )

    async def pending_updates(self, *, limit: int = 32) -> list[Mapping[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM max_webhook_inbox WHERE status = 'pending' "
                "ORDER BY created_at LIMIT ?",
                (limit,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    async def claim_next(
        self, *, control_only: Optional[bool] = None
    ) -> Optional[Mapping[str, Any]]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self._conn.execute(
                    "SELECT event_key, payload FROM max_webhook_inbox "
                    "WHERE status = 'pending' ORDER BY created_at, event_key"
                ).fetchall()
                row = next(
                    (
                        item
                        for item in rows
                        if control_only is None
                        or is_control_update(json.loads(item[1])) is control_only
                    ),
                    None,
                )
                if row is None:
                    self._conn.commit()
                    return None
                self._conn.execute(
                    "UPDATE max_webhook_inbox SET status = 'dispatching', attempts = attempts + 1, "
                    "started_at = ? WHERE event_key = ? AND status = 'pending'",
                    (time.time(), row[0]),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        self._discard_queued_key(str(row[0]))
        return json.loads(row[1])

    async def recover_inflight(self) -> int:
        """Mark work left by a previous Hermes gateway as ambiguous, never replay it."""

        with self._lock:
            cursor = self._conn.execute(
                "UPDATE max_webhook_inbox SET status = 'unknown', last_error = ? "
                "WHERE status IN ('dispatching', 'accepted', 'processing')",
                ("Обработка прервана при остановке; повтор автоматически отключён",),
            )
            self._conn.commit()
            return int(cursor.rowcount)

    def _discard_queued_key(self, event_key: str) -> None:
        self._queued_keys.discard(event_key)
        retained: list[Mapping[str, Any]] = []
        while True:
            try:
                update = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self.queue.task_done()
            if _dedup_key(update) != event_key:
                retained.append(update)
            else:
                self._queued_keys.discard(_dedup_key(update))
        for update in retained:
            try:
                self.queue.put_nowait(update)
                self._queued_keys.add(_dedup_key(update))
            except asyncio.QueueFull:
                break

    async def drain_wakeup_queue(self) -> None:
        """Clear volatile wake-ups; SQLite remains the source of truth."""

        while True:
            try:
                update = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            self.queue.task_done()
            self._queued_keys.discard(_dedup_key(update))

    async def mark_accepted(self, update: Mapping[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE max_webhook_inbox SET status = 'accepted' WHERE event_key = ? "
                "AND status = 'dispatching'",
                (_dedup_key(update),),
            )
            self._conn.commit()

    async def mark_processing_key(self, event_key: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE max_webhook_inbox SET status = 'processing', "
                "started_at = COALESCE(started_at, ?) WHERE event_key = ? "
                "AND status IN ('dispatching', 'accepted')",
                (time.time(), str(event_key)),
            )
            self._conn.commit()

    async def mark_failed_key(self, event_key: str, error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE max_webhook_inbox SET status = 'failed', last_error = ? "
                "WHERE event_key = ? AND status IN ('dispatching', 'accepted', 'processing')",
                (str(error)[:1000], str(event_key)),
            )
            self._conn.commit()

    async def mark_processed_key(self, event_key: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE max_webhook_inbox SET status = 'processed', processed_at = ?, "
                "last_error = NULL WHERE event_key = ? "
                "AND status IN ('pending', 'dispatching', 'accepted', 'processing')",
                (time.time(), str(event_key)),
            )
            self._conn.commit()

    async def mark_unknown_key(self, event_key: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE max_webhook_inbox SET status = 'unknown', last_error = ? "
                "WHERE event_key = ? AND status IN ('dispatching', 'accepted', 'processing')",
                ("Обработка прервана; повтор автоматически отключён", str(event_key)),
            )
            self._conn.commit()

    async def mark_processed(self, update: Mapping[str, Any]) -> None:
        await self.mark_processed_key(_dedup_key(update))

    async def mark_failed(
        self, update: Mapping[str, Any], error: str = "processing failed"
    ) -> None:
        await self.mark_failed_key(_dedup_key(update), error)

    async def mark_processing(self, update: Mapping[str, Any]) -> None:
        await self.mark_processing_key(_dedup_key(update))

    def status_summary(self) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) FROM max_webhook_inbox GROUP BY status"
            ).fetchall()
            last_error = self._conn.execute(
                "SELECT last_error FROM max_webhook_inbox "
                "WHERE last_error IS NOT NULL ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        summary = {str(status): int(count) for status, count in rows}
        for status in ("pending", "dispatching", "accepted", "processing", "processed", "failed", "unknown"):
            summary.setdefault(status, 0)
        summary["last_error"] = str(last_error[0]) if last_error else None
        return summary

    async def next_pending(self) -> Optional[Mapping[str, Any]]:
        try:
            update = self.queue.get_nowait()
            self._queued_keys.discard(_dedup_key(update))
            self.queue.task_done()
            return update
        except asyncio.QueueEmpty:
            pending = await self.pending_updates(limit=1)
            return pending[0] if pending else None

    async def get_queued(self) -> Mapping[str, Any]:
        update = await self.queue.get()
        self._queued_keys.discard(_dedup_key(update))
        return update

    async def close(self) -> None:
        with self._lock:
            self._conn.close()
