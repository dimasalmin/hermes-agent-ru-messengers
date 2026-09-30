"""MAX attachment normalization and URL policy.

The module contains no Hermes imports so the shape and SSRF policy can be
tested without importing the gateway runtime. Actual byte caching is kept in
the adapter and delegated to Hermes' existing media cache.
"""

from __future__ import annotations

import ipaddress
from io import BytesIO
from dataclasses import dataclass
from mimetypes import guess_type
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import unquote, urlsplit

from PIL import Image, UnidentifiedImageError

try:
    from pillow_heif import register_heif_opener
except ImportError:
    _HEIF_AVAILABLE = False
else:
    register_heif_opener(thumbnails=False)
    _HEIF_AVAILABLE = True

MAX_ATTACHMENT_TYPES = frozenset({"image", "video", "audio", "file", "voice"})
MAX_IMAGE_DIMENSION = 7680
DEFAULT_MEDIA_HOSTS = ("max.ru", "oneme.ru", "okcdn.ru")
OUTBOUND_MEDIA_HOSTS = DEFAULT_MEDIA_HOSTS + (
    "fal.media",
    "fal.ai",
    "fal-cdn.com",
    "replicate.delivery",
)
_DEFAULT_MIME = {
    "image": "image/jpeg",
    "video": "video/mp4",
    "audio": "audio/ogg",
    "file": "application/octet-stream",
}
_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".bmp", ".heic"})
_VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".mkv", ".webm"})
_AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".m4a", ".ogg", ".opus", ".flac", ".aac"})


def validate_image_dimensions(
    source: bytes | bytearray | str | Path,
    *,
    filename: Optional[str] = None,
) -> tuple[int, int]:
    """Validate an image header without decoding its pixels into memory."""

    if filename:
        suffix = Path(filename).suffix.lower()
    elif isinstance(source, (bytes, bytearray)):
        suffix = ""
    else:
        suffix = Path(source).suffix.lower()
    if suffix == ".heic" and not _HEIF_AVAILABLE:
        raise ValueError("HEIC dimension validation requires the pillow-heif dependency")
    image_source = BytesIO(source) if isinstance(source, (bytes, bytearray)) else Path(source)
    try:
        with Image.open(image_source) as image:
            width, height = image.size
    except (Image.DecompressionBombError, OSError, UnidentifiedImageError, ValueError) as exc:
        raise ValueError("MAX image is not a readable image") from exc
    if width <= 0 or height <= 0 or max(width, height) > MAX_IMAGE_DIMENSION:
        raise ValueError(
            f"MAX image dimensions must not exceed {MAX_IMAGE_DIMENSION} pixels per side"
        )
    return width, height


@dataclass(frozen=True)
class MaxAttachment:
    """A normalized MAX attachment reference."""

    kind: str
    url: Optional[str]
    filename: str
    mime_type: str
    token: Optional[str] = None


def media_type_for_file(
    file_path: str | Path,
    *,
    is_voice: bool = False,
    force_document: bool = False,
) -> str:
    """Map a Hermes local media path to a MAX upload type."""

    if force_document:
        return "file"
    if is_voice:
        return "audio"
    suffix = Path(file_path).suffix.lower()
    if suffix in _IMAGE_EXTENSIONS:
        return "image"
    if suffix in _VIDEO_EXTENSIONS:
        return "video"
    if suffix in _AUDIO_EXTENSIONS:
        return "audio"
    return "file"


def mime_type_for_file(file_path: str | Path, media_type: str) -> str:
    """Return a conservative MIME type for a MAX upload."""

    guessed = guess_type(str(file_path))[0]
    if guessed:
        return guessed
    return _DEFAULT_MIME.get(media_type, "application/octet-stream")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _url_filename(url: str) -> str:
    path = unquote(urlsplit(url).path)
    filename = path.rsplit("/", 1)[-1].strip()
    # MAX filenames are user-controlled metadata.  Keep a display name but
    # never allow path separators or control characters into the cache path.
    filename = filename.replace("\\", "_").replace("/", "_")
    return "".join(char for char in filename if ord(char) >= 32 and char != "\x7f")[:255]


def attachment_from_payload(attachment: Mapping[str, Any]) -> Optional[MaxAttachment]:
    """Normalize a MAX message attachment.

    MAX commonly nests media fields under ``payload``. A few update shapes
    expose ``url`` or the filename at the attachment level, so both forms are
    accepted. Attachments with a token but no URL are retained and can be
    surfaced as unavailable inbound media rather than silently discarded.
    """

    if not isinstance(attachment, Mapping):
        return None
    raw_kind = _first_text(attachment.get("type")).lower()
    if raw_kind not in MAX_ATTACHMENT_TYPES:
        return None
    kind = "audio" if raw_kind == "voice" else raw_kind
    payload = _mapping(attachment.get("payload"))
    url = _first_text(
        payload.get("url"),
        payload.get("download_url"),
        attachment.get("url"),
        attachment.get("download_url"),
    ) or None
    token = _first_text(
        payload.get("token"),
        payload.get("video_token"),
        attachment.get("token"),
        attachment.get("video_token"),
    ) or None
    filename = _first_text(
        payload.get("filename"),
        payload.get("file_name"),
        payload.get("name"),
        attachment.get("filename"),
        attachment.get("file_name"),
        _url_filename(url or ""),
    )
    if not filename:
        filename = f"max-{kind}"
    mime_type = _first_text(
        payload.get("mime_type"),
        payload.get("mime"),
        attachment.get("mime_type"),
        attachment.get("mime"),
    ).lower() or _DEFAULT_MIME[kind]
    return MaxAttachment(
        kind=kind,
        url=url,
        filename=filename,
        mime_type=mime_type,
        token=token,
    )


def is_allowed_media_url(
    url: str,
    *,
    allowed_hosts: tuple[str, ...] = DEFAULT_MEDIA_HOSTS,
) -> bool:
    """Allow only HTTPS URLs on MAX-owned media host suffixes."""

    try:
        parsed = urlsplit(str(url).strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or not host or parsed.username or parsed.password:
        return False
    try:
        if parsed.port not in (None, 443):
            return False
    except ValueError:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return False
    for suffix in allowed_hosts:
        root = str(suffix).lower().lstrip("*.").rstrip(".")
        if host == root or host.endswith(f".{root}"):
            return True
    return False


def is_allowed_outbound_media_url(url: str) -> bool:
    """Allow HTTPS image sources used by Hermes' native image extractor."""

    return is_allowed_media_url(url, allowed_hosts=OUTBOUND_MEDIA_HOSTS)
