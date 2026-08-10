"""MAX BotAPI rate-limit constants and retry helpers.

MAX BotAPI imposes a soft per-second send budget per bot (verified by Max team).
Limits below are conservative defaults; the adapter retries on HTTP 429 with
``Retry-After`` honored when the upstream library surfaces it.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import defaultdict, deque
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4000
MAX_ATTACHMENT_SIZE = 50 * 1024 * 1024  # 50 MB; refine once API surfaces explicit limit
MAX_ATTACHMENTS_PER_MESSAGE = 10
MAX_GLOBAL_RPS = 30
MAX_CHAT_RPS = 2
SEND_BUDGET_PER_SEC = MAX_GLOBAL_RPS

RETRY_MAX_ATTEMPTS = 4
RETRY_BACKOFF_BASE = 1.5
RETRY_BACKOFF_CAP = 30.0


T = TypeVar("T")


class MaxRateLimiter:
    """Enforce the documented global and per-dialog operation windows."""

    def __init__(self) -> None:
        self._global: deque[float] = deque()
        self._per_chat: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    @staticmethod
    def _prune(window: deque[float], now: float) -> None:
        cutoff = now - 1.0
        while window and window[0] <= cutoff:
            window.popleft()

    def delay_for(self, chat_id: str, now: float | None = None) -> float:
        current = time.monotonic() if now is None else now
        chat_window = self._per_chat[chat_id]
        self._prune(self._global, current)
        self._prune(chat_window, current)
        delays: list[float] = []
        if len(self._global) >= MAX_GLOBAL_RPS:
            delays.append(max(0.0, self._global[0] + 1.0 - current))
        if len(chat_window) >= MAX_CHAT_RPS:
            delays.append(max(0.0, chat_window[0] + 1.0 - current))
        return max(delays, default=0.0)

    def record(self, chat_id: str, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        self._prune(self._global, current)
        chat_window = self._per_chat[chat_id]
        self._prune(chat_window, current)
        self._global.append(current)
        chat_window.append(current)

    async def acquire(self, chat_id: str) -> None:
        async with self._lock:
            while True:
                delay = self.delay_for(chat_id)
                if delay <= 0:
                    self.record(chat_id)
                    return
                await asyncio.sleep(delay)


async def with_backoff(
    fn: Callable[[], Awaitable[T]],
    *,
    is_rate_limit: Callable[[BaseException], bool],
    extract_retry_after: Callable[[BaseException], float | None],
    max_attempts: int = RETRY_MAX_ATTEMPTS,
) -> T:
    """Retry ``fn`` on rate-limit errors with exponential backoff + jitter.

    Non-rate-limit exceptions propagate immediately.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn()
        except BaseException as exc:  # noqa: BLE001 вЂ” caller's is_rate_limit decides
            if not is_rate_limit(exc) or attempt >= max_attempts:
                raise
            wait = extract_retry_after(exc)
            if wait is None:
                wait = min(RETRY_BACKOFF_CAP, RETRY_BACKOFF_BASE ** attempt)
            wait += random.uniform(0, 0.5)
            logger.warning(
                "MAX rate limit on attempt %d/%d; sleeping %.1fs", attempt, max_attempts, wait
            )
            await asyncio.sleep(wait)
