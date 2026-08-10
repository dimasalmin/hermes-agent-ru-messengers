from __future__ import annotations

from plugins.max.rate_limit import MaxRateLimiter


def test_chat_window_limits_to_two_operations_per_second() -> None:
    limiter = MaxRateLimiter()
    now = 100.0
    limiter.record("chat-1", now)
    limiter.record("chat-1", now)

    assert limiter.delay_for("chat-1", now) == 1.0
    assert limiter.delay_for("chat-2", now) == 0.0


def test_old_operations_expire_from_window() -> None:
    limiter = MaxRateLimiter()
    limiter.record("chat-1", 100.0)
    limiter.record("chat-1", 100.0)

    assert limiter.delay_for("chat-1", 101.001) == 0.0
