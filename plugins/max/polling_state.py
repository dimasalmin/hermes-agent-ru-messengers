"""Persistent MAX Long Polling marker store."""

from __future__ import annotations

import sqlite3
import threading
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Mapping, Optional


def max_update_event_key(update: Mapping[str, Any]) -> str:
    """Return a stable per-update key without merging separate button presses."""

    update_id = update.get("update_id")
    if update_id is not None:
        return f"update:{update_id}"
    update_type = str(update.get("update_type") or "update")
    callback = update.get("callback")
    if isinstance(callback, Mapping) and callback.get("callback_id"):
        return f"callback:{callback['callback_id']}"
    message = update.get("message")
    if isinstance(message, Mapping):
        body = message.get("body")
        if isinstance(body, Mapping) and body.get("mid"):
            return f"{update_type}:message:{body['mid']}"
    encoded = json.dumps(update, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "payload:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _event_key(update: Mapping[str, Any]) -> str:
    return max_update_event_key(update)


def is_control_update(update: Mapping[str, Any]) -> bool:
    """Classify callbacks, lifecycle events and urgent commands for priority handling."""

    update_type = str(update.get("update_type") or "")
    if update_type != "message_created":
        return True
    message = update.get("message")
    body = message.get("body") if isinstance(message, Mapping) else None
    if not isinstance(body, Mapping) or body.get("attachments"):
        return False
    text = str(body.get("text") or "").strip()
    if not text.startswith("/"):
        return False
    return True


def migrate_event_keys(conn: sqlite3.Connection, table: str) -> None:
    """Re-key existing inbox rows transactionally while preserving their state."""

    if table not in {"max_polling_inbox", "max_webhook_inbox"}:
        raise ValueError("unsupported MAX inbox table")
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute(
            f"SELECT event_key, payload, status, attempts, created_at, started_at, "
            f"processed_at, last_error FROM {table}"
        ).fetchall()
        for old_key, payload, status, attempts, created_at, started_at, processed_at, last_error in rows:
            new_key = max_update_event_key(json.loads(payload))
            if new_key == old_key:
                continue
            existing = conn.execute(
                f"SELECT status, attempts, created_at, started_at, processed_at, last_error "
                f"FROM {table} WHERE event_key = ?",
                (new_key,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    f"UPDATE {table} SET event_key = ? WHERE event_key = ?",
                    (new_key, old_key),
                )
                continue

            # A legacy key may already have collapsed events. Never turn a
            # previously claimed/failed row back into pending during migration.
            states = (str(status), str(existing[0]))
            merged_status = next(
                (
                    candidate
                    for candidate in (
                        "processed",
                        "unknown",
                        "processing",
                        "accepted",
                        "dispatching",
                        "failed",
                    )
                    if candidate in states
                ),
                "pending",
            )
            conn.execute(
                f"UPDATE {table} SET status = ?, attempts = ?, created_at = ?, "
                f"started_at = ?, processed_at = ?, last_error = ? WHERE event_key = ?",
                (
                    merged_status,
                    max(int(attempts), int(existing[1])),
                    min(float(created_at), float(existing[2])),
                    started_at or existing[3],
                    processed_at or existing[4],
                    last_error or existing[5],
                    new_key,
                ),
            )
            conn.execute(f"DELETE FROM {table} WHERE event_key = ?", (old_key,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


class PollingMarkerStore:
    """Store the last acknowledged MAX marker across gateway restarts."""

    def __init__(self, path: str | Path) -> None:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(target), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS max_polling_state "
                "(name TEXT PRIMARY KEY, marker INTEGER)"
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS max_polling_inbox (
                    event_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    processed_at REAL,
                    last_error TEXT
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS max_polling_inbox_status_idx "
                "ON max_polling_inbox(status, created_at)"
            )
            self._conn.commit()
            migrate_event_keys(self._conn, "max_polling_inbox")

    def get(self) -> Optional[int]:
        with self._lock:
            row = self._conn.execute(
                "SELECT marker FROM max_polling_state WHERE name = 'updates'"
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def set(self, marker: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO max_polling_state(name, marker) VALUES ('updates', ?) "
                "ON CONFLICT(name) DO UPDATE SET marker = excluded.marker",
                (int(marker),),
            )
            self._conn.commit()

    def recover_inflight(self) -> int:
        """Mark work left by a previous gateway process as ambiguous, never replay it."""

        with self._lock:
            cursor = self._conn.execute(
                "UPDATE max_polling_inbox SET status = 'unknown', last_error = ? "
                "WHERE status IN ('dispatching', 'accepted', 'processing')",
                ("Обработка прервана при остановке; повтор автоматически отключён",),
            )
            self._conn.commit()
            return int(cursor.rowcount)

    def accept_batch(
        self,
        updates: list[Mapping[str, Any]],
        marker: Optional[int],
    ) -> None:
        """Persist updates and the next marker in one SQLite transaction."""

        with self._lock:
            self._conn.execute("BEGIN")
            try:
                now = time.time()
                for update in updates:
                    if not isinstance(update, Mapping):
                        continue
                    key = _event_key(update)
                    payload = json.dumps(update, ensure_ascii=False, separators=(",", ":"))
                    self._conn.execute(
                        "INSERT OR IGNORE INTO max_polling_inbox "
                        "(event_key, payload, status, created_at) VALUES (?, ?, 'pending', ?)",
                        (key, payload, now),
                    )
                if marker is not None:
                    self._conn.execute(
                        "INSERT INTO max_polling_state(name, marker) VALUES ('updates', ?) "
                        "ON CONFLICT(name) DO UPDATE SET marker = excluded.marker",
                        (int(marker),),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def claim_next(self, *, control_only: Optional[bool] = None) -> Optional[Mapping[str, Any]]:
        """Atomically claim a pending update; in-flight work is never auto-replayed."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self._conn.execute(
                    "SELECT event_key, payload FROM max_polling_inbox "
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
                    "UPDATE max_polling_inbox SET status = 'dispatching', attempts = attempts + 1, "
                    "started_at = ? WHERE event_key = ? AND status = 'pending'",
                    (time.time(), row[0]),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return json.loads(row[1])

    def mark_accepted(self, update: Mapping[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE max_polling_inbox SET status = 'accepted' WHERE event_key = ? "
                "AND status = 'dispatching'",
                (_event_key(update),),
            )
            self._conn.commit()

    def mark_processing_key(self, event_key: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE max_polling_inbox SET status = 'processing', "
                "started_at = COALESCE(started_at, ?) WHERE event_key = ? "
                "AND status IN ('dispatching', 'accepted')",
                (time.time(), str(event_key)),
            )
            self._conn.commit()

    def mark_failed_key(self, event_key: str, error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE max_polling_inbox SET status = 'failed', last_error = ? "
                "WHERE event_key = ? AND status IN ('dispatching', 'accepted', 'processing')",
                (str(error)[:1000], str(event_key)),
            )
            self._conn.commit()

    def mark_processed_key(self, event_key: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE max_polling_inbox SET status = 'processed', processed_at = ?, "
                "last_error = NULL WHERE event_key = ? "
                "AND status IN ('pending', 'dispatching', 'accepted', 'processing')",
                (time.time(), str(event_key)),
            )
            self._conn.commit()

    def mark_unknown_key(self, event_key: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE max_polling_inbox SET status = 'unknown', last_error = ? "
                "WHERE event_key = ? AND status IN ('dispatching', 'accepted', 'processing')",
                ("Обработка прервана; повтор автоматически отключён", str(event_key)),
            )
            self._conn.commit()

    def mark_processed(self, update: Mapping[str, Any]) -> None:
        self.mark_processed_key(_event_key(update))

    def mark_failed(self, update: Mapping[str, Any], error: str) -> None:
        self.mark_failed_key(_event_key(update), error)

    def status_summary(self) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) FROM max_polling_inbox GROUP BY status"
            ).fetchall()
            last_error = self._conn.execute(
                "SELECT last_error FROM max_polling_inbox "
                "WHERE last_error IS NOT NULL ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        summary = {str(status): int(count) for status, count in rows}
        for status in ("pending", "dispatching", "accepted", "processing", "processed", "failed", "unknown"):
            summary.setdefault(status, 0)
        summary["last_error"] = str(last_error[0]) if last_error else None
        summary["marker"] = self.get()
        return summary

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class MaxTargetStore:
    """Persist whether a target is a MAX user dialog or group chat."""

    def __init__(self, path: str | Path) -> None:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(target), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS max_targets "
                "(chat_id TEXT PRIMARY KEY, target_type TEXT NOT NULL)"
            )
            self._conn.commit()

    def get(self, chat_id: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT target_type FROM max_targets WHERE chat_id = ?", (str(chat_id),)
            ).fetchone()
        return str(row[0]) if row else None

    def set(self, chat_id: str, target_type: str) -> None:
        if target_type not in {"user", "chat"}:
            raise ValueError(f"unsupported MAX target type: {target_type}")
        with self._lock:
            self._conn.execute(
                "INSERT INTO max_targets(chat_id, target_type) VALUES (?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET target_type = excluded.target_type",
                (str(chat_id), target_type),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
