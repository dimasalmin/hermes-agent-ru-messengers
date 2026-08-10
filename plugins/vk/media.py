"""Bounded VK attachment download and Hermes media-cache bridge."""

from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

DEFAULT_MEDIA_MAX_BYTES = 25 * 1024 * 1024
ALLOWED_MEDIA_HOSTS = (
    "vk.com",
    "vk.ru",
    "vkuseraudio.net",
    "vkontakte.ru",
    "userapi.com",
    "vk-cdn.net",
)


@dataclass(frozen=True)
class DownloadedMedia:
    data: bytes
    filename: str
    mime_type: str
    media_type: str


def attachment_url(attachment: dict) -> tuple[str, str, str] | None:
    kind = str(attachment.get("type") or "").lower()
    item = attachment.get(kind)
    if not isinstance(item, dict):
        return None
    if kind == "photo":
        sizes = [entry for entry in item.get("sizes", []) if isinstance(entry, dict) and entry.get("url")]
        if not sizes:
            return None
        selected = max(sizes, key=lambda entry: int(entry.get("width") or 0) * int(entry.get("height") or 0))
        return str(selected["url"]), "photo.jpg", "photo"
    if kind == "audio_message":
        url = item.get("link_ogg") or item.get("link_mp3")
        if url:
            extension = ".ogg" if item.get("link_ogg") else ".mp3"
            return str(url), f"voice{extension}", "voice"
    if kind == "doc" and item.get("url"):
        name = os.path.basename(str(item.get("title") or "document.bin")) or "document.bin"
        return str(item["url"]), name, "document"
    return None


async def download_attachment(
    attachment: dict,
    *,
    max_bytes: int = DEFAULT_MEDIA_MAX_BYTES,
    timeout: float = 30.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> DownloadedMedia | None:
    resolved = attachment_url(attachment)
    if resolved is None:
        return None
    url, filename, media_type = resolved
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not _allowed_host(host):
        return None
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, transport=transport) as client:
        async with client.stream("GET", url) as response:
            if response.is_redirect:
                return None
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > max_bytes:
                raise ValueError("VK attachment exceeds configured size limit")
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes(64 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("VK attachment exceeds configured size limit")
                chunks.append(chunk)
    data = b"".join(chunks)
    return DownloadedMedia(
        data=data,
        filename=filename,
        mime_type=response.headers.get("content-type", "").split(";", 1)[0]
        or mimetypes.guess_type(filename)[0]
        or "application/octet-stream",
        media_type=media_type,
    )


def _allowed_host(host: str) -> bool:
    return any(host == suffix or host.endswith("." + suffix) for suffix in ALLOWED_MEDIA_HOSTS)
