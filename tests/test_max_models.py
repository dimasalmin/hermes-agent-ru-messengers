from __future__ import annotations

from plugins.max.models import MaxCallback, MaxMessage


def test_message_from_update_uses_body_mid_and_recipient_chat_type() -> None:
    update = {
        "update_type": "message_created",
        "timestamp": 1730000000000,
        "message": {
            "sender": {"user_id": 42, "name": "Alice", "is_bot": False},
            "recipient": {"chat_id": 9001, "chat_type": "dialog"},
            "body": {
                "mid": "mid-7",
                "text": "hello",
                "attachments": [{"type": "image", "payload": {"url": "https://img"}}],
            },
        },
    }

    message = MaxMessage.from_update(update)

    assert message is not None
    assert message.message_id == "mid-7"
    assert message.user_id == "42"
    assert message.chat_id == "42"
    assert message.chat_type == "dialog"
    assert message.text == "hello"
    assert message.attachments[0]["type"] == "image"


def test_message_from_update_ignores_non_message_updates() -> None:
    assert MaxMessage.from_update({"update_type": "bot_started"}) is None


def test_message_from_update_marks_bot_sender() -> None:
    update = {
        "update_type": "message_created",
        "message": {
            "sender": {"user_id": 7, "is_bot": True},
            "recipient": {"chat_id": 8, "chat_type": "chat"},
            "body": {"mid": "mid-bot", "text": "echo"},
        },
    }

    message = MaxMessage.from_update(update)

    assert message is not None
    assert message.is_bot is True
    assert message.is_group is True
    assert message.chat_id == "8"


def test_callback_from_update_normalizes_dm_sender_and_message() -> None:
    update = {
        "update_type": "message_callback",
        "callback": {
            "callback_id": "callback-1",
            "payload": "hmx:clarify:opaque-token",
            "user": {"user_id": 100000001, "name": "Alice"},
            "message": {
                "sender": {"user_id": 999, "is_bot": True},
                "recipient": {"chat_type": "dialog", "user_id": 999},
                "body": {"mid": "prompt-1", "text": "Choose"},
            },
        },
    }

    callback = MaxCallback.from_update(update)

    assert callback is not None
    assert callback.callback_id == "callback-1"
    assert callback.payload == "hmx:clarify:opaque-token"
    assert callback.user_id == "100000001"
    assert callback.chat_id == "100000001"
    assert callback.chat_type == "dialog"
    assert callback.message_id == "prompt-1"


def test_callback_from_update_ignores_other_update_types() -> None:
    assert MaxCallback.from_update({"update_type": "message_created"}) is None
