"""Normalized Community Long Poll events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class VkIncomingMessage:
    message_id: str
    peer_id: int
    from_id: int
    text: str
    payload: Any = None
    attachments: tuple[Mapping[str, Any], ...] = ()
    conversation_message_id: str | None = None
    reply_to_message_id: str | None = None
    reply_to_text: str | None = None
    raw_event: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_group(self) -> bool:
        return self.peer_id > 2_000_000_000


@dataclass(frozen=True)
class VkCallback:
    event_id: str
    user_id: str
    peer_id: int
    payload: Any
    raw_event: Mapping[str, Any] = field(default_factory=dict)


def parse_long_poll_event(event: Mapping[str, Any]) -> VkIncomingMessage | VkCallback | None:
    event_type = str(event.get("type") or "")
    obj = event.get("object")
    if not isinstance(obj, Mapping):
        return None
    if event_type == "message_event":
        return VkCallback(
            event_id=str(obj.get("event_id") or ""),
            user_id=str(obj.get("user_id") or ""),
            peer_id=int(obj.get("peer_id") or 0),
            payload=obj.get("payload"),
            raw_event=event,
        )
    if event_type != "message_new":
        return None
    message = obj.get("message") if isinstance(obj.get("message"), Mapping) else obj
    attachments = message.get("attachments") or ()
    reply = message.get("reply_message")
    return VkIncomingMessage(
        message_id=str(message.get("conversation_message_id") or message.get("id") or ""),
        conversation_message_id=(
            str(message.get("conversation_message_id"))
            if message.get("conversation_message_id") is not None
            else None
        ),
        peer_id=int(message.get("peer_id") or 0),
        from_id=int(message.get("from_id") or 0),
        text=str(message.get("text") or ""),
        payload=message.get("payload"),
        attachments=tuple(item for item in attachments if isinstance(item, Mapping)),
        reply_to_message_id=str(reply.get("id")) if isinstance(reply, Mapping) and reply.get("id") else None,
        reply_to_text=str(reply.get("text")) if isinstance(reply, Mapping) and reply.get("text") else None,
        raw_event=event,
    )
