"""MAX Webhook validation with a durable SQLite inbox."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional


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
    update_id = update.get("update_id")
    if update_id is not None:
        return f"update:{update_id}"
    message = update.get("message")
    if isinstance(message, Mapping):
        body = message.get("body")
        if isinstance(body, Mapping) and body.get("mid"):
            return f"message:{body['mid']}"
    encoded = json.dumps(update, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "payload:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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
                    processed_at REAL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS max_webhook_inbox_status_idx "
                "ON max_webhook_inbox(status, created_at)"
            )
            self._conn.commit()

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
            elif row[0] == "processed":
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

    async def mark_processed(self, update: Mapping[str, Any]) -> None:
        key = _dedup_key(update)
        with self._lock:
            self._conn.execute(
                "UPDATE max_webhook_inbox SET status = 'processed', processed_at = ? "
                "WHERE event_key = ?",
                (time.time(), key),
            )
            self._conn.commit()

    async def mark_failed(self, update: Mapping[str, Any]) -> None:
        key = _dedup_key(update)
        with self._lock:
            self._conn.execute(
                "UPDATE max_webhook_inbox SET attempts = attempts + 1 WHERE event_key = ?",
                (key,),
            )
            self._conn.commit()

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
