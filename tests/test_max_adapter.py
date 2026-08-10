"""Smoke tests for MAX adapter (rate-limit helpers and pure utilities).

Full adapter integration tests live in Hermes's own test suite (run via
``scripts/run_tests.sh`` once the plugin is dropped into ``~/.hermes/plugins/``).
This file exercises the parts that don't require importing Hermes core.
"""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType

import pytest

from plugins.max.adapter import MaxAdapter
from plugins.max.common import AccessPolicy
from plugins.max.interactive import MaxCallbackStore
from plugins.max.models import MaxCallback
from plugins.max.rate_limit import (
    MAX_MESSAGE_LENGTH,
    MaxRateLimiter,
    RETRY_BACKOFF_BASE,
    with_backoff,
)


class _InteractiveClient:
    def __init__(self) -> None:
        self.sent = []
        self.answers = []

    async def send_message(self, target_id, text, **kwargs):
        self.sent.append((target_id, text, kwargs))
        return {"message": {"body": {"mid": "prompt-1"}}}

    async def answer_callback(self, callback_id, **kwargs):
        self.answers.append((callback_id, kwargs))
        return {"success": True}


def _interactive_adapter(client: _InteractiveClient) -> MaxAdapter:
    adapter = object.__new__(MaxAdapter)
    adapter._client = client
    adapter._chat_target_types = {}
    adapter._target_store = None
    adapter._rate_limiter = MaxRateLimiter()
    adapter._callbacks = MaxCallbackStore()
    adapter._model_pickers = {}
    adapter._access = AccessPolicy(allowed_users={"user-1"})
    return adapter


def test_max_message_length_matches_documented_limit():
    assert MAX_MESSAGE_LENGTH == 4000


def test_retry_backoff_base_is_above_one():
    assert RETRY_BACKOFF_BASE > 1.0


class _Throttled(Exception):
    status_code = 429


def _always_rl(_exc: BaseException) -> bool:
    return True


def _never_rl(_exc: BaseException) -> bool:
    return False


def _retry_after_zero(_exc: BaseException) -> float:
    return 0.0


@pytest.mark.asyncio
async def test_with_backoff_retries_until_success():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _Throttled("nope")
        return "ok"

    result = await with_backoff(
        fn, is_rate_limit=_always_rl, extract_retry_after=_retry_after_zero, max_attempts=5
    )
    assert result == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_with_backoff_exhausts_attempts():
    async def fn():
        raise _Throttled("nope")

    with pytest.raises(_Throttled):
        await with_backoff(
            fn, is_rate_limit=_always_rl, extract_retry_after=_retry_after_zero, max_attempts=2
        )


@pytest.mark.asyncio
async def test_with_backoff_does_not_retry_non_rate_limit():
    async def fn():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        await with_backoff(
            fn, is_rate_limit=_never_rl, extract_retry_after=lambda _e: None, max_attempts=5
        )


@pytest.mark.asyncio
async def test_send_exec_approval_renders_max_inline_keyboard() -> None:
    client = _InteractiveClient()
    adapter = _interactive_adapter(client)

    result = await adapter.send_exec_approval(
        "user-1", "rm -rf /tmp/example", "session-1", description="test command"
    )

    assert result.success is True
    _target_id, text, kwargs = client.sent[0]
    assert "rm -rf /tmp/example" in text
    buttons = kwargs["attachments"][0]["payload"]["buttons"]
    assert [button["text"] for row in buttons for button in row] == [
        "Р Р°Р·СЂРµС€РёС‚СЊ РѕРґРёРЅ СЂР°Р·",
        "Р Р°Р·СЂРµС€РёС‚СЊ РЅР° СЃРµСЃСЃРёСЋ",
        "Р Р°Р·СЂРµС€РёС‚СЊ РІСЃРµРіРґР°",
        "Р—Р°РїСЂРµС‚РёС‚СЊ",
    ]
    assert all(button["payload"].startswith("hmx:approval:") for row in buttons for button in row)


@pytest.mark.asyncio
async def test_send_model_picker_renders_provider_buttons() -> None:
    client = _InteractiveClient()
    adapter = _interactive_adapter(client)

    result = await adapter.send_model_picker(
        "user-1",
        providers=[
            {
                "slug": "custom",
                "name": "Local model",
                "models": ["qwen/test"],
                "total_models": 1,
                "is_current": True,
            }
        ],
        current_model="qwen/test",
        current_provider="custom",
        session_key="session-1",
        on_model_selected=lambda *_args: "switched",
    )

    assert result.success is True
    _target_id, text, kwargs = client.sent[0]
    assert "qwen/test" in text
    buttons = kwargs["attachments"][0]["payload"]["buttons"]
    assert [button["text"] for row in buttons for button in row] == [
        "вњ“ Local model (1)",
        "РћС‚РјРµРЅР°",
    ]
    assert all(button["payload"].startswith("hmx:model:") for row in buttons for button in row)


@pytest.mark.asyncio
async def test_model_picker_callbacks_navigate_and_switch_model(monkeypatch) -> None:
    client = _InteractiveClient()
    adapter = _interactive_adapter(client)
    selected = []

    async def on_model_selected(chat_id, model_id, provider_slug):
        selected.append((chat_id, model_id, provider_slug))
        return "РњРѕРґРµР»СЊ РїРµСЂРµРєР»СЋС‡РµРЅР°"

    await adapter.send_model_picker(
        "user-1",
        providers=[
            {
                "slug": "custom",
                "name": "Local model",
                "models": ["qwen/test"],
                "total_models": 1,
            }
        ],
        current_model="old/model",
        current_provider="other",
        session_key="session-1",
        on_model_selected=on_model_selected,
    )

    provider_payload = client.sent[0][2]["attachments"][0]["payload"]["buttons"][0][0]["payload"]
    provider_callback = MaxCallback.from_update(
        {
            "update_type": "message_callback",
            "callback": {
                "callback_id": "cb-provider",
                "payload": provider_payload,
                "user": {"user_id": "user-1"},
                "message": {"recipient": {"chat_type": "dialog"}},
            },
        }
    )
    assert provider_callback is not None
    await adapter._dispatch_callback(provider_callback)

    model_message = client.answers[-1][1]["message"]
    assert "Local model" in model_message["text"]
    model_payload = model_message["attachments"][0]["payload"]["buttons"][0][0]["payload"]
    model_callback = MaxCallback.from_update(
        {
            "update_type": "message_callback",
            "callback": {
                "callback_id": "cb-model",
                "payload": model_payload,
                "user": {"user_id": "user-1"},
                "message": {"recipient": {"chat_type": "dialog"}},
            },
        }
    )
    assert model_callback is not None
    await adapter._dispatch_callback(model_callback)

    assert selected == [("user-1", "qwen/test", "custom")]
    assert client.answers[-1][1]["message"]["text"] == "РњРѕРґРµР»СЊ РїРµСЂРµРєР»СЋС‡РµРЅР°"


@pytest.mark.asyncio
async def test_approval_callback_resolves_hermes_and_is_answered(monkeypatch) -> None:
    client = _InteractiveClient()
    adapter = _interactive_adapter(client)
    resolved = []
    approval = ModuleType("tools.approval")
    approval.resolve_gateway_approval = lambda session, choice: resolved.append((session, choice)) or 1
    tools_package = ModuleType("tools")
    tools_package.approval = approval
    monkeypatch.setitem(sys.modules, "tools", tools_package)
    monkeypatch.setitem(sys.modules, "tools.approval", approval)

    payload = adapter._callbacks.issue(
        "approval", "once", user_id="user-1", chat_id="user-1", session_key="session-1"
    )
    callback = MaxCallback.from_update(
        {
            "update_type": "message_callback",
            "callback": {
                "callback_id": "cb-1",
                "payload": payload,
                "user": {"user_id": "user-1"},
                "message": {
                    "recipient": {"chat_type": "dialog"},
                    "body": {"mid": "prompt-1"},
                },
            },
        }
    )

    assert callback is not None
    await adapter._dispatch_callback(callback)

    assert resolved == [("session-1", "once")]
    assert client.answers[0][0] == "cb-1"
    assert client.answers[0][1]["message"]["text"] == "Р Р°Р·СЂРµС€РµРЅРѕ РѕРґРёРЅ СЂР°Р·"


@pytest.mark.asyncio
async def test_clarify_other_callback_enables_gateway_text_capture(monkeypatch) -> None:
    client = _InteractiveClient()
    adapter = _interactive_adapter(client)
    captured = []
    clarify_gateway = ModuleType("tools.clarify_gateway")
    clarify_gateway.mark_awaiting_text = lambda clarify_id: captured.append(clarify_id) or True
    tools_package = ModuleType("tools")
    tools_package.clarify_gateway = clarify_gateway
    monkeypatch.setitem(sys.modules, "tools", tools_package)
    monkeypatch.setitem(sys.modules, "tools.clarify_gateway", clarify_gateway)

    payload = adapter._callbacks.issue(
        "clarify",
        "clarify-1:other",
        user_id="user-1",
        chat_id="user-1",
        session_key="session-1",
    )
    callback = MaxCallback.from_update(
        {
            "update_type": "message_callback",
            "callback": {
                "callback_id": "cb-2",
                "payload": payload,
                "user": {"user_id": "user-1"},
                "message": {"recipient": {"chat_type": "dialog"}},
            },
        }
    )

    assert callback is not None
    await adapter._dispatch_callback(callback)

    assert captured == ["clarify-1"]
    assert client.answers[0][1]["message"]["text"] == "Р’РІРµРґРёС‚Рµ СЃРІРѕР№ РІР°СЂРёР°РЅС‚ СЃР»РµРґСѓСЋС‰РёРј СЃРѕРѕР±С‰РµРЅРёРµРј."
