"""Small, dependency-free MAX update models used by the Hermes adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class MaxMessage:
    """Normalized data extracted from a MAX ``message_created`` update."""

    message_id: str
    user_id: str
    user_name: Optional[str]
    chat_id: str
    chat_type: str
    chat_title: Optional[str]
    text: str
    attachments: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    link: Optional[Mapping[str, Any]] = None
    is_bot: bool = False
    timestamp: Optional[int] = None
    raw_message: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_group(self) -> bool:
        return self.chat_type in {"chat", "group", "channel"}

    @classmethod
    def from_update(cls, update: Mapping[str, Any]) -> Optional["MaxMessage"]:
        if update.get("update_type") != "message_created":
            return None

        message = _mapping(update.get("message"))
        body = _mapping(message.get("body"))
        sender = _mapping(message.get("sender"))
        recipient = _mapping(message.get("recipient"))

        message_id = str(body.get("mid") or "").strip()
        if not message_id:
            return None

        user_id = str(sender.get("user_id") or recipient.get("user_id") or "").strip()
        chat_type = str(recipient.get("chat_type") or "dialog").strip().lower()
        # MAX addresses direct-message sends by user_id.  A dialog update may
        # still include an internal recipient chat_id, but /messages expects
        # the sender's user_id for that target type.
        if chat_type in {"chat", "group", "channel"}:
            chat_id = str(recipient.get("chat_id") or user_id).strip()
        else:
            chat_id = user_id
        attachments = tuple(
            item for item in (body.get("attachments") or []) if isinstance(item, Mapping)
        )

        return cls(
            message_id=message_id,
            user_id=user_id,
            user_name=sender.get("name") or sender.get("username"),
            chat_id=chat_id,
            chat_type=chat_type,
            chat_title=recipient.get("chat_title") or recipient.get("title"),
            text=str(body.get("text") or ""),
            attachments=attachments,
            link=message.get("link") if isinstance(message.get("link"), Mapping) else None,
            is_bot=bool(sender.get("is_bot", False)),
            timestamp=message.get("timestamp") or update.get("timestamp"),
            raw_message=message,
        )


@dataclass(frozen=True)
class MaxCallback:
    """Normalized data extracted from a MAX ``message_callback`` update."""

    callback_id: str
    payload: str
    user_id: str
    user_name: Optional[str]
    chat_id: str
    chat_type: str
    message_id: Optional[str]
    raw_callback: Mapping[str, Any] = field(default_factory=dict)
    raw_message: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_group(self) -> bool:
        return self.chat_type in {"chat", "group", "channel"}

    @classmethod
    def from_update(cls, update: Mapping[str, Any]) -> Optional["MaxCallback"]:
        if update.get("update_type") != "message_callback":
            return None

        callback = _mapping(update.get("callback"))
        message = _mapping(callback.get("message") or update.get("message"))
        sender = _mapping(message.get("sender"))
        user = _mapping(
            callback.get("user")
            or callback.get("sender")
            or update.get("user")
            or sender
        )
        recipient = _mapping(message.get("recipient"))

        callback_id = str(callback.get("callback_id") or update.get("callback_id") or "").strip()
        payload = str(callback.get("payload") or update.get("payload") or "")
        user_id = str(user.get("user_id") or user.get("id") or "").strip()
        if not callback_id or not payload or not user_id:
            return None

        chat_type = str(
            recipient.get("chat_type")
            or callback.get("chat_type")
            or update.get("chat_type")
            or "dialog"
        ).strip().lower()
        if chat_type in {"chat", "group", "channel"}:
            chat_id = str(
                recipient.get("chat_id")
                or callback.get("chat_id")
                or update.get("chat_id")
                or user_id
            ).strip()
        else:
            chat_id = user_id
        if not chat_id:
            return None

        body = _mapping(message.get("body"))
        message_id = str(
            body.get("mid")
            or message.get("message_id")
            or message.get("mid")
            or callback.get("message_id")
            or ""
        ).strip() or None
        return cls(
            callback_id=callback_id,
            payload=payload,
            user_id=user_id,
            user_name=user.get("name") or user.get("username"),
            chat_id=chat_id,
            chat_type=chat_type,
            message_id=message_id,
            raw_callback=callback,
            raw_message=message,
        )
