from __future__ import annotations

import inspect
from types import SimpleNamespace

import plugins.max.adapter as adapter_module
from plugins.max.adapter import (
    MaxAdapter,
    _build_message_event,
    _send_result_from_ids,
    apply_yaml_config,
)
from plugins.max.models import MaxMessage


def test_connect_accepts_hermes_reconnect_keyword() -> None:
    signature = inspect.signature(MaxAdapter.connect)
    assert "is_reconnect" in signature.parameters
    assert signature.parameters["is_reconnect"].default is False


def test_message_event_uses_adapter_build_source(monkeypatch) -> None:
    class FakeEvent:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    monkeypatch.setattr(adapter_module, "MessageEvent", FakeEvent)
    monkeypatch.setattr(adapter_module, "MessageType", SimpleNamespace(TEXT="text"))

    adapter = object.__new__(MaxAdapter)
    adapter.platform = "max"
    calls = {}

    def build_source(**kwargs):
        calls.update(kwargs)
        return "source"

    adapter.build_source = build_source
    message = MaxMessage(
        message_id="mid-1",
        user_id="42",
        user_name="Alice",
        chat_id="9001",
        chat_type="dialog",
        chat_title=None,
        text="hello",
    )

    event = _build_message_event(adapter, message)

    assert event.source == "source"
    assert event.message_id == "mid-1"
    assert event.text == "hello"
    assert calls["chat_id"] == "9001"
    assert calls["user_id"] == "42"
    assert calls["chat_type"] == "dm"
    assert calls["message_id"] == "mid-1"


def test_yaml_hook_returns_extra_without_overwriting_environment(monkeypatch) -> None:
    monkeypatch.setenv("MAX_API_BASE_URL", "https://env.example")

    extra = apply_yaml_config(
        {"platforms": {"max": {"api_base_url": "https://yaml.example"}}},
        {"api_base_url": "https://yaml.example", "webhook_url": "https://hook.example/max"},
    )

    assert extra["api_base_url"] == "https://env.example"
    assert extra["webhook_url"] == "https://hook.example/max"


def test_send_result_marks_last_message_and_keeps_prior_continuations() -> None:
    result = _send_result_from_ids(["first", "last"])

    assert result.message_id == "last"
    assert result.continuation_message_ids == ("first",)


def test_reply_link_uses_max_mid_field() -> None:
    assert adapter_module._reply_link("in-1") == {"type": "reply", "mid": "in-1"}
    assert adapter_module._reply_link(None) is None
