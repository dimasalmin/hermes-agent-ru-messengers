"""Short-lived, single-use state for MAX inline-button callbacks."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from secrets import token_urlsafe
from typing import Callable, Mapping, Optional

CALLBACK_PREFIX = "hmx"
_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


@dataclass(frozen=True)
class MaxCallbackEntry:
    kind: str
    value: str
    user_id: str
    chat_id: str
    session_key: str
    expires_at: float


class MaxCallbackStore:
    """In-memory callback registry with expiry and replay protection.

    The registry intentionally is not persisted.  A gateway restart invalidates
    outstanding buttons instead of retaining approval state without the live
    Hermes wait primitive that owns it.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = 600.0,
        max_entries: int = 512,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("callback TTL must be positive")
        if max_entries <= 0:
            raise ValueError("callback store capacity must be positive")
        self._ttl_seconds = float(ttl_seconds)
        self._max_entries = int(max_entries)
        self._clock = clock
        self._entries: dict[str, MaxCallbackEntry] = {}
        self._lock = threading.Lock()

    def issue(
        self,
        kind: str,
        value: str,
        *,
        user_id: str,
        chat_id: str,
        session_key: str,
    ) -> str:
        kind = str(kind).strip()
        if not _KIND_PATTERN.fullmatch(kind):
            raise ValueError("callback kind contains unsupported characters")
        user_id = str(user_id).strip()
        chat_id = str(chat_id).strip()
        if not user_id or not chat_id:
            raise ValueError("callback user_id and chat_id are required")

        now = self._clock()
        expires_at = now + self._ttl_seconds
        with self._lock:
            self._prune(now)
            token = token_urlsafe(12)
            while token in self._entries:
                token = token_urlsafe(12)
            self._entries[token] = MaxCallbackEntry(
                kind=kind,
                value=str(value),
                user_id=user_id,
                chat_id=chat_id,
                session_key=str(session_key),
                expires_at=expires_at,
            )
        return f"{CALLBACK_PREFIX}:{kind}:{token}"

    def consume(
        self,
        payload: str,
        *,
        user_id: str,
        chat_id: str,
    ) -> Optional[MaxCallbackEntry]:
        parsed = self.parse(payload)
        if parsed is None:
            return None
        kind, token = parsed
        now = self._clock()
        with self._lock:
            entry = self._entries.get(token)
            if entry is None:
                return None
            if entry.kind != kind:
                return None
            if entry.expires_at <= now:
                del self._entries[token]
                return None
            if entry.user_id != str(user_id).strip() or entry.chat_id != str(chat_id).strip():
                return None
            del self._entries[token]
            return entry

    def _prune(self, now: float) -> None:
        expired = [token for token, entry in self._entries.items() if entry.expires_at <= now]
        for token in expired:
            del self._entries[token]
        overflow = len(self._entries) - self._max_entries + 1
        if overflow > 0:
            oldest = sorted(self._entries, key=lambda token: self._entries[token].expires_at)
            for token in oldest[:overflow]:
                del self._entries[token]

    @staticmethod
    def parse(payload: str) -> Optional[tuple[str, str]]:
        parts = str(payload or "").split(":", 2)
        if len(parts) != 3 or parts[0] != CALLBACK_PREFIX or not _KIND_PATTERN.fullmatch(parts[1]):
            return None
        token = parts[2].strip()
        return (parts[1], token) if token else None


def build_inline_keyboard(rows: list[list[Mapping[str, str]]]) -> dict:
    """Build the MAX attachment shape for callback buttons."""

    return {
        "type": "inline_keyboard",
        "payload": {"buttons": [[dict(button) for button in row] for row in rows]},
    }
