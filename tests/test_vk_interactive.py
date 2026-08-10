from __future__ import annotations

import pytest

from plugins.vk.interactive import CallbackStore, build_inline_keyboard, build_keyboard


def test_callback_store_binds_payload_to_user_peer_session_and_is_single_use():
    store = CallbackStore(ttl_seconds=60, now=lambda: 100.0)
    payload = store.issue("approval", "once", user_id="100000001", peer_id="100000001", session_key="vk:100000001")

    assert store.consume(payload, user_id="other", peer_id="100000001", session_key="vk:100000001") is None
    assert store.consume(payload, user_id="100000001", peer_id="100000001", session_key="wrong") is None
    assert store.consume(payload, user_id="100000001", peer_id="100000001", session_key="vk:100000001") == {
        "kind": "approval",
        "action": "once",
    }
    assert store.consume(payload, user_id="100000001", peer_id="100000001", session_key="vk:100000001") is None


def test_callback_store_expires_payloads_and_keyboard_has_vk_shape():
    now = [100.0]
    store = CallbackStore(ttl_seconds=10, now=lambda: now[0])
    payload = store.issue("x", "y", user_id="1", peer_id="1", session_key="s")
    now[0] = 111.0
    assert store.consume(payload, user_id="1", peer_id="1", session_key="s") is None

    keyboard = build_inline_keyboard([[{"label": "Allow", "payload": "abc", "color": "positive"}]])
    assert keyboard == {
        "inline": True,
        "buttons": [[{"action": {"type": "callback", "label": "Allow", "payload": "abc"}, "color": "positive"}]],
    }


def test_vk_keyboard_builder_supports_text_and_open_link_actions_with_limits():
    keyboard = build_keyboard(
        [[
            {"label": "Run", "action_type": "text"},
            {"label": "Docs", "action_type": "open_link", "link": "https://example.com"},
        ]],
        inline=False,
    )
    assert keyboard["inline"] is False
    assert keyboard["buttons"][0][0]["action"] == {"type": "text", "label": "Run"}
    assert keyboard["buttons"][0][1]["action"] == {
        "type": "open_link",
        "label": "Docs",
        "link": "https://example.com",
    }


def test_vk_inline_keyboard_rejects_more_than_ten_buttons():
    with pytest.raises(ValueError, match="too many buttons"):
        build_inline_keyboard([[{"label": str(i), "payload": "x"}] for i in range(11)])
