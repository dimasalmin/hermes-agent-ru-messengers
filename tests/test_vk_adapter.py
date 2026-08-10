"""Smoke tests for VK adapter (peer_id semantics, rate-limit helpers, mention regex)."""

from __future__ import annotations

import pytest

from plugins.vk.adapter import _is_chat_peer, _is_command, _random_id
from plugins.vk.rate_limit import (
    VK_MESSAGE_LENGTH,
    is_vk_rate_limit,
    reconnect_delay,
    with_backoff,
)


def test_vk_message_length_matches_api_limit():
    assert VK_MESSAGE_LENGTH == 4096


def test_is_chat_peer_distinguishes_dm_from_chat():
    assert _is_chat_peer(2_000_000_001) is True
    assert _is_chat_peer(2_000_000_000) is False  # boundary: not a chat
    assert _is_chat_peer(12345) is False


def test_is_command_detects_leading_slash():
    assert _is_command("/help") is True
    assert _is_command("   /model arg") is True
    assert _is_command("hello /not_a_command") is False
    assert _is_command("") is False


def test_random_id_is_in_int32_range():
    for _ in range(50):
        rid = _random_id()
        assert 0 <= rid < 2_147_483_647


class _VkError(Exception):
    code = 6  # "too many requests per second"


def test_is_vk_rate_limit_recognizes_code_6_and_9():
    assert is_vk_rate_limit(_VkError("x")) is True
    other = type("OtherErr", (Exception,), {"code": 9})("flood")
    assert is_vk_rate_limit(other("x") if callable(other) else other) is True
    assert is_vk_rate_limit(ValueError("nope")) is False


def test_reconnect_delay_is_bounded_and_increases_with_attempts():
    assert reconnect_delay(0, rng=lambda: 0.0) == 0.5
    assert reconnect_delay(2, rng=lambda: 0.0) == 2.0
    assert reconnect_delay(20, cap=5.0, rng=lambda: 1.0) == 5.0


@pytest.mark.asyncio
async def test_with_backoff_retries_rate_limit():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] < 2:
            raise _VkError("rl")
        return 42

    result = await with_backoff(fn, max_attempts=3)
    assert result == 42
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_with_backoff_propagates_non_rate_limit():
    async def fn():
        raise ValueError("not throttled")

    with pytest.raises(ValueError):
        await with_backoff(fn, max_attempts=3)
