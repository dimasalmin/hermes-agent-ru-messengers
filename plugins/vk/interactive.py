"""Bound callback tokens and VK inline keyboard serialization."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass
class _Callback:
    kind: str
    action: str
    user_id: str
    peer_id: str
    session_key: str
    expires_at: float


class CallbackStore:
    def __init__(self, *, ttl_seconds: float = 900, now: Callable[[], float] = time.time) -> None:
        self._ttl_seconds = float(ttl_seconds)
        self._now = now
        self._items: dict[str, _Callback] = {}

    def issue(self, kind: str, action: str, *, user_id: str, peer_id: str, session_key: str) -> str:
        self._purge()
        payload = secrets.token_urlsafe(18)
        self._items[payload] = _Callback(
            kind=str(kind),
            action=str(action),
            user_id=str(user_id),
            peer_id=str(peer_id),
            session_key=str(session_key),
            expires_at=self._now() + self._ttl_seconds,
        )
        return payload

    def consume(self, payload: str, *, user_id: str, peer_id: str, session_key: str) -> dict[str, str] | None:
        item = self._items.get(str(payload))
        if item is None or item.expires_at <= self._now():
            self._items.pop(str(payload), None)
            return None
        if (item.user_id, item.peer_id, item.session_key) != (str(user_id), str(peer_id), str(session_key)):
            return None
        del self._items[str(payload)]
        return {"kind": item.kind, "action": item.action}

    def consume_for_context(self, payload: str, *, user_id: str, peer_id: str) -> dict[str, str] | None:
        """Consume after checking the immutable VK callback identity.

        VK callback updates carry user and peer IDs, but not Hermes' internal
        session key.  The stored session key is returned to the resolver,
        while the platform identity remains the authorization boundary.
        """

        item = self._items.get(str(payload))
        if item is None or item.expires_at <= self._now():
            self._items.pop(str(payload), None)
            return None
        if (item.user_id, item.peer_id) != (str(user_id), str(peer_id)):
            return None
        del self._items[str(payload)]
        return {"kind": item.kind, "action": item.action, "session_key": item.session_key}

    def _purge(self) -> None:
        now = self._now()
        for payload, item in list(self._items.items()):
            if item.expires_at <= now:
                del self._items[payload]


def build_keyboard(
    rows: list[list[Mapping[str, Any]]], *, inline: bool = True
) -> dict[str, Any]:
    max_rows = 6 if inline else 10
    max_buttons = 10 if inline else 40
    if sum(len(row) for row in rows) > max_buttons:
        raise ValueError("VK keyboard has too many buttons")
    if len(rows) > max_rows:
        raise ValueError("VK keyboard has too many rows")
    result: list[list[dict[str, Any]]] = []
    for row in rows:
        if not row or len(row) > 5:
            raise ValueError("VK keyboard row must contain 1 to 5 buttons")
        encoded_row: list[dict[str, Any]] = []
        for button in row:
            label = str(button.get("label") or "")
            action_type = str(button.get("action_type") or "callback")
            if not label or len(label) > 40:
                raise ValueError("VK keyboard button label is invalid")
            if action_type not in {"callback", "text", "open_link", "location", "open_app"}:
                raise ValueError("VK keyboard action type is invalid")
            action: dict[str, Any] = {"type": action_type, "label": label}
            payload = button.get("payload")
            if action_type in {"callback", "text"} and payload is not None:
                payload_text = str(payload)
                if len(payload_text.encode("utf-8")) > 255:
                    raise ValueError("VK keyboard payload is invalid")
                action["payload"] = payload_text
            if action_type == "callback" and "payload" not in action:
                raise ValueError("VK callback button payload is required")
            if action_type == "open_link":
                link = str(button.get("link") or "")
                if not link.startswith(("https://", "http://")):
                    raise ValueError("VK open_link button URL is invalid")
                action["link"] = link
            if action_type == "location":
                action.pop("label", None)
            if action_type == "open_app":
                for key in ("app_id", "owner_id", "hash"):
                    if key not in button:
                        raise ValueError("VK open_app button is incomplete")
                    action[key] = button[key]
            encoded: dict[str, Any] = {"action": action}
            color = button.get("color")
            if color:
                if str(color) not in {"primary", "secondary", "positive", "negative"}:
                    raise ValueError("VK callback button color is invalid")
                encoded["color"] = str(color)
            encoded_row.append(encoded)
        result.append(encoded_row)
    return {"inline": bool(inline), "buttons": result}


def build_inline_keyboard(rows: list[list[Mapping[str, Any]]]) -> dict[str, Any]:
    return build_keyboard(rows, inline=True)
