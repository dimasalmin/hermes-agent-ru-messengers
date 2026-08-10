"""Persistent MAX Long Polling marker store."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Optional


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
            self._conn.commit()

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
