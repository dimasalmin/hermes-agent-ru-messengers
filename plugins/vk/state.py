"""Durable, single-process state for VK Long Poll delivery."""

from __future__ import annotations

import sqlite3
import hashlib
import hmac
import secrets
import time
from pathlib import Path
from typing import Optional


STATE_SCHEMA_VERSION = 1


class VkStateStore:
    def __init__(self, path: str, *, seen_ttl_seconds: float = 7 * 24 * 3600) -> None:
        self.path = path
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA busy_timeout=5000")
        if path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS vk_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS vk_seen_messages (
                message_id TEXT PRIMARY KEY,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS vk_lock (
                lock_name TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                lease_until REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS vk_pairing (
                user_id TEXT PRIMARY KEY,
                code_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                expires_at REAL NOT NULL,
                approved_at REAL
            );
            """
        )
        self._set("schema_version", str(STATE_SCHEMA_VERSION))
        self._db.commit()
        self._seen_ttl_seconds = float(seen_ttl_seconds)

    def close(self) -> None:
        self._db.close()

    def claim_message(self, message_id: str) -> bool:
        if not message_id:
            return True
        cutoff = time.time() - self._seen_ttl_seconds
        self._db.execute("DELETE FROM vk_seen_messages WHERE created_at < ?", (cutoff,))
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO vk_seen_messages(message_id, created_at) VALUES (?, ?)",
            (str(message_id), time.time()),
        )
        self._db.commit()
        return cursor.rowcount == 1

    def set_long_poll_state(self, server: str, key: str, ts: str) -> None:
        self._set("long_poll_server", server)
        self._set("long_poll_key", key)
        self._set("long_poll_ts", ts)
        self._db.commit()

    def get_long_poll_state(self) -> Optional[tuple[str, str, str]]:
        values = {key: value for key, value in self._db.execute("SELECT key, value FROM vk_state")}
        if not all(values.get(key) for key in ("long_poll_server", "long_poll_key", "long_poll_ts")):
            return None
        return values["long_poll_server"], values["long_poll_key"], values["long_poll_ts"]

    def schema_version(self) -> int:
        value = self._db.execute(
            "SELECT value FROM vk_state WHERE key = 'schema_version'"
        ).fetchone()
        try:
            return int(value[0]) if value else 0
        except (TypeError, ValueError):
            return 0

    def acquire_poll_lock(
        self,
        owner_id: str,
        *,
        ttl_seconds: float = 180.0,
        now: float | None = None,
    ) -> bool:
        """Acquire a lease so two gateway processes cannot poll one group."""

        if not owner_id:
            raise ValueError("owner_id must not be empty")
        now = time.time() if now is None else float(now)
        lease_until = now + max(1.0, float(ttl_seconds))
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute(
                "SELECT owner_id, lease_until FROM vk_lock WHERE lock_name = 'poll'"
            ).fetchone()
            if row and str(row[0]) != owner_id and float(row[1]) > now:
                self._db.rollback()
                return False
            self._db.execute(
                """
                INSERT INTO vk_lock(lock_name, owner_id, lease_until)
                VALUES ('poll', ?, ?)
                ON CONFLICT(lock_name) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    lease_until = excluded.lease_until
                """,
                (owner_id, lease_until),
            )
            self._db.commit()
            return True
        except Exception:
            self._db.rollback()
            raise

    def refresh_poll_lock(
        self,
        owner_id: str,
        *,
        ttl_seconds: float = 180.0,
        now: float | None = None,
    ) -> bool:
        now = time.time() if now is None else float(now)
        cursor = self._db.execute(
            """
            UPDATE vk_lock SET lease_until = ?
            WHERE lock_name = 'poll' AND owner_id = ?
            """,
            (now + max(1.0, float(ttl_seconds)), owner_id),
        )
        self._db.commit()
        return cursor.rowcount == 1

    def release_poll_lock(self, owner_id: str) -> bool:
        cursor = self._db.execute(
            "DELETE FROM vk_lock WHERE lock_name = 'poll' AND owner_id = ?",
            (owner_id,),
        )
        self._db.commit()
        return cursor.rowcount == 1

    def issue_pairing_code(
        self,
        user_id: str,
        *,
        ttl_seconds: float = 600.0,
        now: float | None = None,
    ) -> str:
        if not user_id:
            raise ValueError("user_id must not be empty")
        now = time.time() if now is None else float(now)
        code = f"{secrets.randbelow(1_000_000):06d}"
        salt = secrets.token_hex(16)
        code_hash = _pairing_hash(code, salt)
        self._db.execute(
            """
            INSERT INTO vk_pairing(user_id, code_hash, salt, expires_at, approved_at)
            VALUES (?, ?, ?, ?, NULL)
            ON CONFLICT(user_id) DO UPDATE SET
                code_hash = excluded.code_hash,
                salt = excluded.salt,
                expires_at = excluded.expires_at,
                approved_at = NULL
            """,
            (str(user_id), code_hash, salt, now + max(1.0, float(ttl_seconds))),
        )
        self._db.commit()
        return code

    def approve_pairing(self, user_id: str, code: str, *, now: float | None = None) -> bool:
        now = time.time() if now is None else float(now)
        row = self._db.execute(
            "SELECT code_hash, salt, expires_at, approved_at FROM vk_pairing WHERE user_id = ?",
            (str(user_id),),
        ).fetchone()
        if not row or row[3] is not None or float(row[2]) < now:
            return False
        if not hmac.compare_digest(str(row[0]), _pairing_hash(str(code), str(row[1]))):
            return False
        self._db.execute(
            "UPDATE vk_pairing SET approved_at = ? WHERE user_id = ?",
            (now, str(user_id)),
        )
        self._db.commit()
        return True

    def is_paired(self, user_id: str) -> bool:
        row = self._db.execute(
            "SELECT approved_at FROM vk_pairing WHERE user_id = ?",
            (str(user_id),),
        ).fetchone()
        return bool(row and row[0] is not None)

    def revoke_pairing(self, user_id: str) -> bool:
        cursor = self._db.execute(
            "DELETE FROM vk_pairing WHERE user_id = ?", (str(user_id),)
        )
        self._db.commit()
        return cursor.rowcount == 1

    def _set(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO vk_state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


def _pairing_hash(code: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode("utf-8")).hexdigest()
