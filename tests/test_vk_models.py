from __future__ import annotations

from plugins.vk.models import VkCallback, VkIncomingMessage, parse_long_poll_event


def test_message_new_is_normalized_from_community_long_poll_shape():
    event = parse_long_poll_event(
        {
            "type": "message_new",
            "object": {
                "message": {
                    "id": 42,
                    "conversation_message_id": 17,
                    "peer_id": 2_000_000_123,
                    "from_id": 100000001,
                    "text": "@bot status",
                    "attachments": [{"type": "photo", "photo": {"sizes": []}}],
                    "reply_message": {"id": 9, "text": "previous"},
                }
            },
            "group_id": 123,
        }
    )

    assert isinstance(event, VkIncomingMessage)
    assert event.message_id == "17"
    assert event.peer_id == 2_000_000_123
    assert event.from_id == 100000001
    assert event.attachments[0]["type"] == "photo"
    assert event.reply_to_message_id == "9"


def test_message_event_callback_is_normalized_and_payload_stays_opaque():
    event = parse_long_poll_event(
        {
            "type": "message_event",
            "object": {"event_id": "evt-1", "payload": "opaque", "user_id": 100000001, "peer_id": 100000001},
            "group_id": 123,
        }
    )

    assert isinstance(event, VkCallback)
    assert event.event_id == "evt-1"
    assert event.payload == "opaque"
    assert event.user_id == "100000001"


def test_message_new_normalizes_button_payload():
    event = parse_long_poll_event(
        {
            "type": "message_new",
            "object": {
                "message": {
                    "id": 1,
                    "peer_id": 100000001,
                    "from_id": 100000001,
                    "text": "Run",
                    "payload": {"action": "run"},
                }
            },
        }
    )
    assert isinstance(event, VkIncomingMessage)
    assert event.payload == {"action": "run"}
