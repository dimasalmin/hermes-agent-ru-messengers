"""Minimal MAX Bot API v2 HTTP client.

The client deliberately uses the documented REST surface instead of the
older third-party Python SDK.  This keeps the Hermes plugin independent from
SDK release timing and makes authorization/TLS behavior testable.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import urljoin

import httpx

from .media import is_allowed_media_url

DEFAULT_API_BASE = "https://platform-api2.max.ru"
DEFAULT_TIMEOUT = httpx.Timeout(connect=15.0, read=60.0, write=60.0, pool=15.0)
_SECRET_PATTERN = re.compile(r"^[A-Za-z0-9_-]{5,256}$")
MAX_UPLOAD_TYPES = frozenset({"image", "video", "audio", "file"})
DEFAULT_MEDIA_MAX_BYTES = 50 * 1024 * 1024


class MaxApiError(RuntimeError):
    """An API or transport error with machine-readable retry information."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
        retry_after: Optional[float] = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.retry_after = retry_after
        self.retryable = retryable


def _retry_after(response: httpx.Response) -> Optional[float]:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class MaxClient:
    """Async client for the current MAX Bot API v2 endpoint."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_API_BASE,
        timeout: httpx.Timeout | float = DEFAULT_TIMEOUT,
        verify: bool | str = True,
        http_client: Optional[httpx.AsyncClient] = None,
        media_http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        token = token.strip()
        if not token:
            raise ValueError("MAX bot token must not be empty")
        self._token = token
        self._timeout = timeout
        self._verify = verify
        self.base_url = base_url.rstrip("/")
        self._http = http_client or httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": token},
            timeout=timeout,
            verify=verify,
        )
        # Keep CDN/upload traffic on a separate client without default auth
        # headers. The bot token must never be sent to a signed media URL.
        self._media_http = media_http_client
        # A supplied client is primarily a test/integration seam.  Its
        # existing base URL and transport remain intact, but auth is owned by
        # this client so callers cannot accidentally omit the header.
        self._http.headers["Authorization"] = token

    async def close(self) -> None:
        await self._http.aclose()
        if self._media_http is not None and self._media_http is not self._http:
            await self._media_http.aclose()

    def _get_media_http(self) -> httpx.AsyncClient:
        if self._media_http is None:
            self._media_http = httpx.AsyncClient(
                timeout=self._timeout,
                verify=self._verify,
                follow_redirects=False,
            )
        return self._media_http

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json: Optional[Mapping[str, Any]] = None,
        files: Any = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Any:
        try:
            response = await self._http.request(
                method,
                path,
                params=params,
                json=json,
                files=files,
                headers=headers,
            )
        except httpx.RequestError as exc:
            raise MaxApiError(
                f"MAX transport error: {exc.__class__.__name__}",
                retryable=True,
            ) from exc

        try:
            payload = response.json()
        except ValueError:
            payload = None

        if response.status_code >= 400:
            error = payload if isinstance(payload, Mapping) else {}
            code = error.get("code")
            detail = error.get("message") or response.reason_phrase or "MAX API error"
            retryable = response.status_code == 429 or response.status_code >= 500
            raise MaxApiError(
                f"MAX API {response.status_code}: {detail}",
                status_code=response.status_code,
                code=str(code) if code else None,
                retry_after=_retry_after(response),
                retryable=retryable,
            )

        return payload if payload is not None else {}

    async def get_me(self) -> Mapping[str, Any]:
        return await self._request("GET", "/me")

    async def download_media(
        self,
        url: str,
        *,
        max_bytes: int = DEFAULT_MEDIA_MAX_BYTES,
    ) -> tuple[bytes, str]:
        """Download one inbound MAX attachment with bounded size and redirects."""

        if not is_allowed_media_url(url):
            raise MaxApiError("MAX media URL is not allowed")
        if max_bytes <= 0:
            raise ValueError("MAX media max_bytes must be positive")

        current_url = str(url)
        media_http = self._get_media_http()
        try:
            for _ in range(4):
                if not is_allowed_media_url(current_url):
                    raise MaxApiError("MAX media redirect URL is not allowed")
                async with media_http.stream(
                    "GET", current_url, follow_redirects=False
                ) as response:
                    if 300 <= response.status_code < 400:
                        location = response.headers.get("Location")
                        if not location:
                            raise MaxApiError("MAX media redirect has no location")
                        current_url = urljoin(current_url, location)
                        continue
                    if response.status_code >= 400:
                        raise MaxApiError(
                            f"MAX media download HTTP {response.status_code}",
                            status_code=response.status_code,
                            retryable=response.status_code == 429 or response.status_code >= 500,
                            retry_after=_retry_after(response),
                        )
                    content_length = response.headers.get("Content-Length")
                    try:
                        if content_length is not None and int(content_length) > max_bytes:
                            raise MaxApiError("MAX media attachment exceeds configured size limit")
                    except ValueError:
                        pass
                    data = bytearray()
                    async for chunk in response.aiter_bytes(64 * 1024):
                        data.extend(chunk)
                        if len(data) > max_bytes:
                            raise MaxApiError("MAX media attachment exceeds configured size limit")
                    content_type = response.headers.get("Content-Type", "")
                    return bytes(data), content_type.split(";", 1)[0].strip().lower()
            raise MaxApiError("MAX media redirect limit exceeded")
        except httpx.RequestError as exc:
            raise MaxApiError(
                f"MAX media transport error: {exc.__class__.__name__}",
                retryable=True,
            ) from exc

    async def upload_media(
        self,
        file_path: str | Path,
        *,
        media_type: str,
        max_bytes: int = DEFAULT_MEDIA_MAX_BYTES,
        mime_type: Optional[str] = None,
    ) -> Mapping[str, Any]:
        """Upload a local file using the current MAX two-step token flow."""

        media_type = str(media_type).strip().lower()
        if media_type not in MAX_UPLOAD_TYPES:
            raise ValueError(f"Unsupported MAX upload type: {media_type}")
        path = Path(file_path)
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise MaxApiError(f"MAX media file is not readable: {path.name}") from exc
        if size > max_bytes:
            raise MaxApiError("MAX media file exceeds configured size limit")

        upload_info = await self._request(
            "POST", "/uploads", params={"type": media_type}
        )
        upload_url = str(upload_info.get("url") or "").strip()
        if not upload_url or not is_allowed_media_url(upload_url):
            raise MaxApiError("MAX returned an invalid media upload URL")

        content_type = mime_type or {
            "image": "image/jpeg",
            "video": "video/mp4",
            "audio": "audio/ogg",
            "file": "application/octet-stream",
        }[media_type]
        media_http = self._get_media_http()
        try:
            with path.open("rb") as file_obj:
                response = await media_http.post(
                    upload_url,
                    files={"data": (path.name, file_obj, content_type)},
                    headers={"Authorization": self._token, "Accept": "application/json"},
                    follow_redirects=False,
                )
        except (OSError, httpx.RequestError) as exc:
            raise MaxApiError(
                f"MAX media upload transport error: {exc.__class__.__name__}",
                retryable=True,
            ) from exc

        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400:
            error = payload if isinstance(payload, Mapping) else {}
            detail = error.get("message") or response.reason_phrase or "MAX upload error"
            raise MaxApiError(
                f"MAX media upload HTTP {response.status_code}: {detail}",
                status_code=response.status_code,
                code=str(error.get("code")) if error.get("code") else None,
                retry_after=_retry_after(response),
                retryable=response.status_code == 429 or response.status_code >= 500,
            )
        if not isinstance(payload, Mapping):
            payload = {}
        result = dict(payload)
        if not result.get("token") and upload_info.get("token"):
            result["token"] = upload_info["token"]
        if not result.get("token"):
            raise MaxApiError("MAX media upload returned no attachment token")
        return result

    async def send_message(
        self,
        target_id: str,
        text: str,
        *,
        target_type: str = "user",
        link: Optional[Mapping[str, Any]] = None,
        attachments: Optional[Iterable[Mapping[str, Any]]] = None,
        notify: bool = True,
        format: str = "markdown",
    ) -> Mapping[str, Any]:
        if len(text) > 4000:
            raise ValueError("MAX message text must not exceed 4000 characters")
        if target_type not in {"user", "chat"}:
            raise ValueError("target_type must be 'user' or 'chat'")
        body: dict[str, Any] = {"text": text, "notify": notify, "format": format}
        if link is not None:
            body["link"] = dict(link)
        if attachments is not None:
            body["attachments"] = [dict(item) for item in attachments]
        return await self._request(
            "POST",
            "/messages",
            params={"user_id" if target_type == "user" else "chat_id": target_id},
            json=body,
        )

    async def edit_message(
        self,
        message_id: str,
        text: str,
        *,
        attachments: Optional[Iterable[Mapping[str, Any]]] = None,
        notify: bool = True,
        format: str = "markdown",
    ) -> Mapping[str, Any]:
        if len(text) > 4000:
            raise ValueError("MAX message text must not exceed 4000 characters")
        body: dict[str, Any] = {"text": text, "notify": notify, "format": format}
        if attachments is not None:
            body["attachments"] = [dict(item) for item in attachments]
        return await self._request(
            "PUT",
            "/messages",
            params={"message_id": message_id},
            json=body,
        )

    async def answer_callback(
        self,
        callback_id: str,
        *,
        message: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        """Acknowledge a button click and optionally replace its message."""

        callback_id = str(callback_id).strip()
        if not callback_id:
            raise ValueError("MAX callback_id must not be empty")
        body: dict[str, Any] = {}
        if message is not None:
            body["message"] = dict(message)
        return await self._request(
            "POST",
            "/answers",
            params={"callback_id": callback_id},
            json=body,
        )

    async def subscribe_webhook(
        self,
        url: str,
        secret: str,
        *,
        update_types: Optional[Iterable[str]] = None,
    ) -> Mapping[str, Any]:
        if not url.startswith("https://"):
            raise ValueError("MAX Webhook URL must use HTTPS")
        if not _SECRET_PATTERN.fullmatch(secret):
            raise ValueError("MAX Webhook secret must match [A-Za-z0-9_-]{5,256}")
        body: dict[str, Any] = {"url": url, "secret": secret}
        if update_types is not None:
            body["update_types"] = list(update_types)
        return await self._request("POST", "/subscriptions", json=body)

    async def get_subscriptions(self) -> Any:
        return await self._request("GET", "/subscriptions")

    async def get_updates(
        self,
        *,
        marker: Optional[int] = None,
        timeout: int = 30,
        limit: int = 100,
        types: Optional[Iterable[str]] = None,
    ) -> Mapping[str, Any]:
        if not 0 <= timeout <= 90:
            raise ValueError("MAX polling timeout must be between 0 and 90 seconds")
        if not 1 <= limit <= 1000:
            raise ValueError("MAX polling limit must be between 1 and 1000")
        params: dict[str, Any] = {"timeout": timeout, "limit": limit}
        if marker is not None:
            params["marker"] = marker
        if types is not None:
            params["types"] = ",".join(types)
        return await self._request("GET", "/updates", params=params)
