"""Small, testable VK Community API and Long Poll transport.

The transport deliberately uses VK's documented HTTP API directly.  This keeps
the plugin independent from a framework's polling lifecycle and makes retry,
redaction and error handling explicit.
"""

from __future__ import annotations

import logging
import mimetypes
import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import httpx

from .rate_limit import is_vk_rate_limit

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://api.vk.com/method"
DEFAULT_API_VERSION = "5.199"


class VkApiError(RuntimeError):
    """A redacted VK API or transport error."""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        self.code = code
        self.error_code = code
        self.retryable = retryable
        self.retry_after = retry_after
        super().__init__(message)


@dataclass(frozen=True)
class VkLongPollResult:
    ts: str
    updates: tuple[Mapping[str, Any], ...] = ()
    failed: int = 0
    retry_after: float | None = None

    @property
    def needs_reinit(self) -> bool:
        return self.failed in {2, 3, 4}


class VkApiClient:
    """Async client for VK Community API and Community Long Poll."""

    def __init__(
        self,
        token: str,
        group_id: int,
        *,
        api_base: str = DEFAULT_API_BASE,
        api_version: str = DEFAULT_API_VERSION,
        timeout: float = 35.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not token.strip():
            raise ValueError("VK token must not be empty")
        if int(group_id) <= 0:
            raise ValueError("VK group_id must be positive")
        self.group_id = int(group_id)
        self._token = token
        self._api_version = api_version
        self._http = httpx.AsyncClient(
            base_url=api_base.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": "hermes-agent-vk-plugin/0.1"},
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def call(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        data = {str(k): v for k, v in (params or {}).items() if v is not None}
        data.update({"access_token": self._token, "v": self._api_version})
        try:
            response = await self._http.post(f"/{method}", data=data)
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise VkApiError("VK API request timed out", retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            retryable = exc.response.status_code >= 500
            raise VkApiError(
                f"VK API HTTP status {exc.response.status_code}", retryable=retryable
            ) from exc
        except httpx.HTTPError as exc:
            raise VkApiError("VK API network request failed", retryable=True) from exc
        except ValueError as exc:
            raise VkApiError("VK API returned invalid JSON", retryable=True) from exc

        error = payload.get("error") if isinstance(payload, Mapping) else None
        if error:
            code = _int_or_none(error.get("error_code"))
            message = str(error.get("error_msg") or "VK API error")
            raise VkApiError(
                f"VK API error {code or 'unknown'}: {message}",
                code=code,
                retryable=is_vk_rate_limit(type("VkError", (), {"code": code})())
                or code in {10, 14, 500, 503},
            )
        if not isinstance(payload, Mapping) or "response" not in payload:
            raise VkApiError("VK API response has no response field", retryable=True)
        return payload["response"]

    async def get_long_poll_server(self) -> Mapping[str, Any]:
        result = await self.call("groups.getLongPollServer", {"group_id": self.group_id})
        if not isinstance(result, Mapping) or not result.get("server") or not result.get("key"):
            raise VkApiError("VK Long Poll server response is incomplete", retryable=True)
        return result

    async def long_poll_check(
        self,
        server: str,
        key: str,
        ts: str,
        *,
        wait: int = 25,
    ) -> VkLongPollResult:
        params = {"act": "a_check", "key": key, "ts": ts, "wait": max(1, min(int(wait), 90))}
        try:
            response = await self._http.get(server, params=params, timeout=max(wait + 10, 30))
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise VkApiError("VK Long Poll request timed out", retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            status = int(exc.response.status_code)
            raise VkApiError(
                f"VK Long Poll HTTP status {status}",
                code=status,
                retryable=status >= 500 or status in {400, 403, 404},
            ) from exc
        except httpx.HTTPError as exc:
            raise VkApiError("VK Long Poll network request failed", retryable=True) from exc
        except ValueError as exc:
            raise VkApiError("VK Long Poll returned invalid JSON", retryable=True) from exc

        if not isinstance(payload, Mapping):
            raise VkApiError("VK Long Poll response is not an object", retryable=True)
        failed = _int_or_none(payload.get("failed")) or 0
        next_ts = str(payload.get("ts") or ts)
        updates = payload.get("updates") or ()
        if not isinstance(updates, (list, tuple)):
            updates = ()
        return VkLongPollResult(
            ts=next_ts,
            updates=tuple(item for item in updates if isinstance(item, Mapping)),
            failed=failed,
        )

    async def send_message(
        self,
        peer_id: int,
        message: str,
        *,
        random_id: int,
        reply_to: int | None = None,
        attachment: str | None = None,
        keyboard: Mapping[str, Any] | None = None,
        format_data: Mapping[str, Any] | None = None,
    ) -> int:
        result = await self.call(
            "messages.send",
            {
                "peer_id": int(peer_id),
                "message": message,
                "random_id": int(random_id),
                "reply_to": reply_to,
                "attachment": attachment,
                "keyboard": _json_param(keyboard),
                "format_data": _json_param(format_data),
            },
        )
        return int(result)

    async def edit_message(
        self,
        peer_id: int,
        conversation_message_id: int,
        message: str,
        *,
        format_data: Mapping[str, Any] | None = None,
    ) -> int:
        result = await self.call(
            "messages.edit",
            {
                "peer_id": int(peer_id),
                "conversation_message_id": int(conversation_message_id),
                "message": message,
                "format_data": _json_param(format_data),
            },
        )
        return int(result or 0)

    async def set_typing(self, peer_id: int) -> Any:
        return await self.call("messages.setActivity", {"peer_id": int(peer_id), "type": "typing"})

    async def answer_message_event(
        self,
        event_id: str,
        user_id: int,
        *,
        event_data: str | None = None,
    ) -> Any:
        return await self.call(
            "messages.sendMessageEventAnswer",
            {"event_id": event_id, "user_id": int(user_id), "event_data": event_data},
        )

    async def upload_file(
        self,
        upload_url: str,
        path: str,
        *,
        field_name: str,
        max_bytes: int,
    ) -> Mapping[str, Any]:
        size = os.path.getsize(path)
        if size > max_bytes:
            raise VkApiError(f"VK media file exceeds {max_bytes} bytes")
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        try:
            with open(path, "rb") as handle:
                response = await self._http.post(
                    upload_url,
                    files={field_name: (os.path.basename(path), handle, mime)},
                )
            response.raise_for_status()
            payload = response.json()
        except (OSError, httpx.TimeoutException) as exc:
            raise VkApiError("VK media upload failed", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise VkApiError("VK media upload returned an HTTP error", retryable=True) from exc
        except ValueError as exc:
            raise VkApiError("VK media upload returned invalid JSON", retryable=True) from exc
        if not isinstance(payload, Mapping):
            raise VkApiError("VK media upload response is invalid", retryable=True)
        return payload

    async def upload_photo(self, peer_id: int, path: str, *, max_bytes: int) -> str:
        upload = await self.call("photos.getMessagesUploadServer", {"peer_id": peer_id})
        uploaded = await self.upload_file(upload["upload_url"], path, field_name="photo", max_bytes=max_bytes)
        saved = await self.call(
            "photos.saveMessagesPhoto",
            {"photo": uploaded.get("photo"), "server": uploaded.get("server"), "hash": uploaded.get("hash")},
        )
        item = saved[0] if isinstance(saved, list) and saved else saved
        if not isinstance(item, Mapping) or not item.get("owner_id") or item.get("id") is None:
            raise VkApiError("VK photo save response is incomplete")
        return f"photo{item['owner_id']}_{item['id']}"

    async def upload_document(self, peer_id: int, path: str, *, max_bytes: int) -> str:
        return await self._upload_document_like(peer_id, path, max_bytes=max_bytes, doc_type="doc")

    async def upload_audio(self, peer_id: int, path: str, *, max_bytes: int) -> str:
        return await self._upload_document_like(
            peer_id, path, max_bytes=max_bytes, doc_type="audio_message"
        )

    async def _upload_document_like(
        self,
        peer_id: int,
        path: str,
        *,
        max_bytes: int,
        doc_type: str,
    ) -> str:
        upload = await self.call(
            "docs.getMessagesUploadServer",
            {"peer_id": peer_id, "type": doc_type},
        )
        uploaded = await self.upload_file(upload["upload_url"], path, field_name="file", max_bytes=max_bytes)
        saved = await self.call(
            "docs.save",
            {"file": uploaded.get("file"), "title": os.path.basename(path)},
        )
        item = saved.get("doc") if isinstance(saved, Mapping) else saved
        if isinstance(item, list):
            item = item[0] if item else None
        if not isinstance(item, Mapping) or not item.get("owner_id") or item.get("id") is None:
            raise VkApiError("VK document save response is incomplete")
        return f"doc{item['owner_id']}_{item['id']}"


def _json_param(value: Mapping[str, Any] | None) -> str | None:
    if value is None:
        return None
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
