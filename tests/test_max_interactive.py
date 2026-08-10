from __future__ import annotations

from plugins.max.interactive import MaxCallbackStore, build_inline_keyboard


def test_callback_store_is_opaque_single_use_and_bound_to_user_and_chat() -> None:
    now = [100.0]
    store = MaxCallbackStore(ttl_seconds=30, clock=lambda: now[0])

    payload = store.issue(
        "approval",
        "once",
        user_id="user-1",
        chat_id="chat-1",
        session_key="session-secret",
    )

    assert "once" not in payload
    assert "session-secret" not in payload
    assert store.consume(payload, user_id="user-2", chat_id="chat-1") is None
    assert store.consume(payload, user_id="user-1", chat_id="chat-2") is None
    assert store.consume(payload.replace(":approval:", ":clarify:"), user_id="user-1", chat_id="chat-1") is None

    entry = store.consume(payload, user_id="user-1", chat_id="chat-1")
    assert entry is not None
    assert entry.kind == "approval"
    assert entry.value == "once"
    assert entry.session_key == "session-secret"
    assert store.consume(payload, user_id="user-1", chat_id="chat-1") is None


def test_callback_store_expires_entries() -> None:
    now = [100.0]
    store = MaxCallbackStore(ttl_seconds=10, clock=lambda: now[0])
    payload = store.issue(
        "clarify",
        "0",
        user_id="user-1",
        chat_id="chat-1",
        session_key="session-1",
    )

    now[0] = 110.01

    assert store.consume(payload, user_id="user-1", chat_id="chat-1") is None


def test_inline_keyboard_uses_max_callback_attachment_shape() -> None:
    assert build_inline_keyboard(
        [[{"type": "callback", "text": "OK", "payload": "hmx:approval:x"}]]
    ) == {
        "type": "inline_keyboard",
        "payload": {
            "buttons": [[
                {"type": "callback", "text": "OK", "payload": "hmx:approval:x"}
            ]]
        },
    }
