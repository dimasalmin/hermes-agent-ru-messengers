from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

import plugins.max.adapter as adapter_module
from plugins.max.adapter import (
    MaxAdapter,
    _message_type,
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
    assert adapter_module._reply_message_id({"type": "reply", "mid": "in-1"}) == "in-1"
    assert adapter_module._reply_message_id(
        {"type": "reply", "message": {"body": {"mid": "in-2"}}}
    ) == "in-2"


def test_group_reply_is_a_mention_only_when_linked_sender_is_the_bot() -> None:
    bot_id = "900"
    assert adapter_module._is_reply_to_bot(
        {"type": "reply", "sender": {"user_id": 900, "is_bot": True}}, bot_id
    )
    assert not adapter_module._is_reply_to_bot(
        {"type": "reply", "sender": {"user_id": 42, "is_bot": False}}, bot_id
    )
    assert not adapter_module._is_reply_to_bot(
        {"type": "forward", "sender": {"user_id": 900, "is_bot": True}}, bot_id
    )
    assert not adapter_module._is_reply_to_bot(
        {"sender": {"user_id": 900, "is_bot": True}}, bot_id
    )


@pytest.mark.asyncio
async def test_media_downloads_are_serial_per_chat_and_parallel_across_chats() -> None:
    adapter = object.__new__(MaxAdapter)
    adapter._media_download_semaphore = asyncio.Semaphore(2)
    adapter._chat_media_download_locks = {}
    active = 0
    maximum_active = 0

    async def populate(_message, _event):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.02)
        active -= 1

    adapter._populate_message_media = populate

    def message(chat_id: str, user_id: str) -> MaxMessage:
        return MaxMessage(
            message_id=f"{chat_id}-{user_id}",
            user_id=user_id,
            user_name=None,
            chat_id=chat_id,
            chat_type="chat",
            chat_title=None,
            text="",
            attachments=({"type": "file"},),
        )

    await asyncio.gather(
        adapter._download_media_for_chat(message("group-1", "user-1"), None),
        adapter._download_media_for_chat(message("group-1", "user-2"), None),
    )
    assert maximum_active == 1

    maximum_active = 0
    await asyncio.gather(
        adapter._download_media_for_chat(message("group-2", "user-3"), None),
        adapter._download_media_for_chat(message("group-3", "user-4"), None),
    )
    assert maximum_active == 2


def test_max_menu_uses_installed_gateway_registry_and_plugin_diagnostics() -> None:
    adapter = object.__new__(MaxAdapter)

    commands = adapter._max_commands()
    names = [item["name"] for item in commands]

    assert len(commands) <= 32
    assert "menu" in names
    assert "commands" in names
    assert "maxstatus" in names
    assert "status" in names
    assert all(item["name"] == item["name"].lower() for item in commands)


def test_max_menu_text_contains_explicit_command_list() -> None:
    adapter = object.__new__(MaxAdapter)

    text = adapter._command_list_text()

    # The menu must remain visible even when a MAX client hides the native bot menu.
    assert "Доступные команды Hermes:" in text
    assert "/commands" in text
    assert "/maxstatus" in text


@pytest.mark.asyncio
async def test_max_command_registration_uses_visible_command_list() -> None:
    adapter = object.__new__(MaxAdapter)
    captured: list[dict[str, str]] = []

    class Client:
        async def set_bot_commands(self, commands: list[dict[str, str]]) -> None:
            captured.extend(commands)

    adapter._client = Client()

    await adapter._register_commands()

    names = [item["name"] for item in captured]
    assert "commands" in names
    assert "maxstatus" in names


def test_incoming_video_maps_to_video_message_type() -> None:
    message = MaxMessage(
        message_id="video-1",
        user_id="42",
        user_name="Alice",
        chat_id="42",
        chat_type="dialog",
        chat_title=None,
        text="",
        attachments=({"type": "video", "payload": {"token": "token"}},),
    )

    assert getattr(_message_type(message), "value", _message_type(message)) == "video"


def test_group_message_scopes_hermes_session_by_participant() -> None:
    class FakeEvent:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    original_event = adapter_module.MessageEvent
    adapter_module.MessageEvent = FakeEvent
    try:
        adapter = object.__new__(MaxAdapter)
        adapter.platform = "max"
        adapter.build_source = lambda **kwargs: SimpleNamespace(**kwargs)
        message = MaxMessage(
            message_id="mid-group",
            user_id="42",
            user_name="Alice",
            chat_id="9001",
            chat_type="chat",
            chat_title="Group",
            text="hello",
        )

        event = _build_message_event(adapter, message)
    finally:
        adapter_module.MessageEvent = original_event

    assert event.source.chat_id == "9001::user::42"
    assert event.metadata["max_chat_id"] == "9001"
    assert event.metadata["max_target_type"] == "chat"


@pytest.mark.asyncio
async def test_processing_hooks_track_hermes_start_and_completion(tmp_path) -> None:
    from plugins.max.polling_state import PollingMarkerStore

    update = {"update_type": "message_created", "update_id": 22}
    store = PollingMarkerStore(tmp_path / "polling.sqlite3")
    store.accept_batch([update], 22)
    store.claim_next()
    adapter = object.__new__(MaxAdapter)
    adapter._webhook_mode = False
    adapter._marker_store = store
    adapter._receiver = None
    event = SimpleNamespace(metadata={"max_update_event_key": "update:22"})

    await adapter.on_processing_start(event)
    assert store.status_summary()["processing"] == 1
    await adapter.on_processing_complete(event, "SUCCESS")
    assert store.status_summary()["processed"] == 1
    store.close()


@pytest.mark.asyncio
async def test_polling_continues_receiving_while_workers_process_media(tmp_path) -> None:
    from plugins.max.polling_state import PollingMarkerStore

    next_poll = asyncio.Event()

    class Client:
        calls = 0

        async def get_updates(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {"marker": 31, "updates": [{"update_type": "message_created", "update_id": 31}]}
            next_poll.set()
            await asyncio.Future()

    adapter = object.__new__(MaxAdapter)
    adapter._running = True
    adapter._client = Client()
    adapter._polling_timeout = 90
    adapter._marker_store = PollingMarkerStore(tmp_path / "polling.sqlite3")
    adapter._inbox_wakeup = asyncio.Event()
    task = asyncio.create_task(adapter._poll_updates())
    try:
        await asyncio.wait_for(next_poll.wait(), timeout=1)
        assert adapter._marker_store.status_summary()["pending"] == 1
        assert adapter._marker_store.get() == 31
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        adapter._marker_store.close()


@pytest.mark.asyncio
async def test_reserved_control_worker_runs_while_media_dispatch_is_blocked(tmp_path) -> None:
    from plugins.max.polling_state import PollingMarkerStore

    store = PollingMarkerStore(tmp_path / "polling.sqlite3")
    media = {"update_type": "message_created", "update_id": 41}
    callback = {"update_type": "message_callback", "callback": {"callback_id": "cb-42"}}
    store.accept_batch([media, callback], 42)
    media_started = asyncio.Event()
    callback_done = asyncio.Event()
    release_media = asyncio.Event()
    adapter = object.__new__(MaxAdapter)
    adapter._running = True
    adapter._webhook_mode = False
    adapter._marker_store = store
    adapter._receiver = None
    adapter._inbox_wakeup = asyncio.Event()
    adapter._inbox_wakeup.set()

    async def dispatch(update):
        if update.get("update_type") == "message_created":
            media_started.set()
            await release_media.wait()
        else:
            callback_done.set()
        return "processed"

    adapter._dispatch_update = dispatch
    workers = [
        asyncio.create_task(adapter._consume_durable_updates(control_only=True)),
        asyncio.create_task(adapter._consume_durable_updates(control_only=False)),
    ]
    try:
        await asyncio.wait_for(media_started.wait(), timeout=1)
        await asyncio.wait_for(callback_done.wait(), timeout=1)
        assert store.status_summary()["dispatching"] == 1
        assert store.status_summary()["processed"] == 1
    finally:
        release_media.set()
        adapter._running = False
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        store.close()
