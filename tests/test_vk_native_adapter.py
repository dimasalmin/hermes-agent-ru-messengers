from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugins.vk.adapter import VkAdapter, env_enablement, is_connected, standalone_send, validate_config
from plugins.vk.state import VkStateStore


def _config(**extra):
    return SimpleNamespace(token="token", extra={"group_id": 123, **extra})


def test_vk_configuration_requires_group_id_without_touching_hermes_core():
    assert validate_config(_config()) is True
    assert is_connected(_config()) is True
    assert validate_config(SimpleNamespace(token="token", extra={})) is False
    assert validate_config(SimpleNamespace(token=None, extra={"token": "token", "group_id": 123})) is True


def test_vk_env_enablement_seeds_flat_group_id_and_token(monkeypatch):
    monkeypatch.setenv("VK_GROUP_TOKEN", "token")
    monkeypatch.setenv("VK_GROUP_ID", "123")
    assert env_enablement() == {"enabled": True, "token": "token", "group_id": "123"}


def test_vk_adapter_exposes_redacted_health_and_policy_modes():
    adapter = VkAdapter(
        _config(dm_policy="pairing", group_policy="disabled", poll_lock_ttl_seconds=45)
    )
    assert adapter._dm_policy == "pairing"
    assert adapter._group_policy == "disabled"
    assert adapter._poll_lock_ttl == 45.0
    health = adapter.health_snapshot()
    assert health["connected"] is False
    assert health["poll_lock_held"] is False
    assert "token" not in health
    assert "group_id" in health


def test_vk_adapter_reads_allowlist_from_hermes_dotenv(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / ".env").write_text("VK_ALLOWED_USERS=100000001\nVK_DM_POLICY=allowlist\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("VK_ALLOWED_USERS", raising=False)
    monkeypatch.delenv("VK_DM_POLICY", raising=False)

    adapter = VkAdapter(_config())
    assert adapter._can_dm("100000001") is True
    assert adapter._can_dm("3237809") is False


@pytest.mark.asyncio
async def test_vk_pairing_command_approves_user_before_dispatch():
    adapter = VkAdapter(_config(dm_policy="pairing"))
    adapter._state = VkStateStore(":memory:")
    code = adapter._state.issue_pairing_code("100000001")
    sent = []
    events = []

    class FakeClient:
        async def send_message(self, peer_id, message, **kwargs):
            sent.append((peer_id, message, kwargs))
            return 99

    async def collect(event):
        events.append(event)

    adapter._client = FakeClient()
    adapter.handle_message = collect  # type: ignore[method-assign]
    await adapter._dispatch_update(
        {
            "type": "message_new",
            "object": {
                "message": {
                    "id": 1,
                    "peer_id": 100000001,
                    "from_id": 100000001,
                    "text": f"/pair {code}",
                }
            },
        }
    )
    assert adapter._state.is_paired("100000001") is True
    assert sent and "РџРѕРґРєР»СЋС‡РµРЅРёРµ РїРѕРґС‚РІРµСЂР¶РґРµРЅРѕ" in sent[0][1]
    assert events == []

    await adapter._dispatch_update(
        {
            "type": "message_new",
            "object": {
                "message": {
                    "id": 2,
                    "peer_id": 100000001,
                    "from_id": 100000001,
                    "text": "hello",
                }
            },
        }
    )
    assert len(events) == 1
    adapter._state.close()


@pytest.mark.asyncio
async def test_vk_send_uses_direct_client_and_chunks_text():
    adapter = VkAdapter(_config())
    calls = []

    class FakeClient:
        async def send_message(self, peer_id, message, **kwargs):
            calls.append((peer_id, message, kwargs))
            return len(calls)

    adapter._client = FakeClient()
    result = await adapter.send("100000001", "x" * 5000)

    assert result.success is True
    assert len(calls) == 2
    assert all(call[0] == 100000001 for call in calls)
    assert all(len(call[1]) <= 4096 for call in calls)


@pytest.mark.asyncio
async def test_vk_send_renders_markdown_as_format_data():
    adapter = VkAdapter(_config())
    calls = []

    class FakeClient:
        async def send_message(self, peer_id, message, **kwargs):
            calls.append((peer_id, message, kwargs))
            return 12

    adapter._client = FakeClient()
    result = await adapter.send("100000001", "**РџСЂРёРІРµС‚**")

    assert result.success is True
    assert calls[0][1] == "РџСЂРёРІРµС‚"
    assert calls[0][2]["format_data"]["items"][0]["type"] == "bold"


@pytest.mark.asyncio
async def test_vk_clarify_sends_native_inline_keyboard_bound_to_context():
    adapter = VkAdapter(_config())
    calls = []

    class FakeClient:
        async def send_message(self, peer_id, message, **kwargs):
            calls.append((peer_id, message, kwargs))
            return 11

    adapter._client = FakeClient()
    adapter._chat_users["100000001"] = "100000001"
    result = await adapter.send_clarify(
        "100000001",
        "Choose",
        ["one", "two"],
        "clarify-1",
        "vk:100000001",
    )

    assert result.success is True
    keyboard = calls[0][2]["keyboard"]
    assert keyboard["inline"] is True
    assert keyboard["buttons"][0][0]["action"]["type"] == "callback"
    payload = keyboard["buttons"][0][0]["action"]["payload"]
    assert adapter._callbacks.consume_for_context(payload, user_id="100000001", peer_id="100000001") == {
        "kind": "clarify",
        "action": "clarify-1:0",
        "session_key": "vk:100000001",
    }


@pytest.mark.asyncio
async def test_vk_send_keyboard_supports_chat_keyboard_actions():
    adapter = VkAdapter(_config())
    calls = []

    class FakeClient:
        async def send_message(self, peer_id, message, **kwargs):
            calls.append((peer_id, message, kwargs))
            return 22

    adapter._client = FakeClient()
    result = await adapter.send_keyboard(
        "100000001",
        "Р’С‹Р±РµСЂРёС‚Рµ",
        [[{"label": "Р”РѕРєСѓРјРµРЅС‚Р°С†РёСЏ", "action_type": "open_link", "link": "https://example.com"}]],
        inline=False,
    )

    assert result.success is True
    assert calls[0][2]["keyboard"]["inline"] is False
    assert calls[0][2]["format_data"] is None


@pytest.mark.asyncio
async def test_vk_media_upload_failure_falls_back_to_text():
    adapter = VkAdapter(_config())
    sent = []

    class FakeClient:
        async def upload_document(self, *args, **kwargs):
            raise RuntimeError("upload unavailable")

        async def send_message(self, peer_id, message, **kwargs):
            sent.append((peer_id, message, kwargs))
            return 33

    adapter._client = FakeClient()
    result = await adapter.send("100000001", "caption MEDIA:/tmp/report.pdf")

    assert result.success is True
    assert sent and "caption" in sent[0][1]
    assert "MEDIA:" not in sent[0][1]


@pytest.mark.asyncio
async def test_vk_message_new_routes_through_hermes_event_and_deduplicates():
    adapter = VkAdapter(_config(allow_from=["100000001"], require_mention=False))
    adapter._state = VkStateStore(":memory:")
    events = []

    async def collect(event):
        events.append(event)

    adapter.handle_message = collect  # type: ignore[method-assign]
    update = {
        "type": "message_new",
        "object": {"message": {"id": 77, "peer_id": 100000001, "from_id": 100000001, "text": "hello"}},
    }

    await adapter._dispatch_update(update)
    await adapter._dispatch_update(update)

    assert len(events) == 1
    assert events[0].text == "hello"
    assert events[0].source.chat_id == "100000001"
    assert events[0].source.user_id == "100000001"
    adapter._state.close()


def test_standalone_sender_matches_hermes_delivery_contract():
    assert "pconfig" in standalone_send.__annotations__ or standalone_send.__code__.co_argcount >= 3
