from __future__ import annotations

import pytest
import httpx
from types import SimpleNamespace

import plugins.max.adapter as max_adapter_module
from plugins.max.adapter import _build_message_event
from plugins.max.adapter import standalone_send
from plugins.max.client import MaxClient
from plugins.max.models import MaxMessage
from plugins.max.media import (
    MaxAttachment,
    attachment_from_payload,
    is_allowed_media_url,
    media_type_for_file,
    mime_type_for_file,
)


def test_attachment_from_payload_extracts_nested_url_and_filename() -> None:
    attachment = attachment_from_payload(
        {
            "type": "image",
            "payload": {
                "url": "https://iu.oneme.ru/attachments/photo-123.png?sig=secret",
                "filename": "screen.png",
                "mime_type": "image/png",
            },
        }
    )

    assert attachment == MaxAttachment(
        kind="image",
        url="https://iu.oneme.ru/attachments/photo-123.png?sig=secret",
        filename="screen.png",
        mime_type="image/png",
    )


def test_attachment_from_payload_normalizes_voice_to_audio() -> None:
    attachment = attachment_from_payload(
        {
            "type": "voice",
            "payload": {"url": "https://vu.okcdn.ru/voice.ogg"},
        }
    )

    assert attachment is not None
    assert attachment.kind == "audio"
    assert attachment.filename == "voice.ogg"


def test_attachment_from_payload_uses_safe_url_basename() -> None:
    attachment = attachment_from_payload(
        {
            "type": "file",
            "url": "https://fu.oneme.ru/a/../report.pdf?token=hidden",
        }
    )

    assert attachment is not None
    assert attachment.filename == "report.pdf"
    assert attachment.mime_type == "application/octet-stream"


def test_attachment_from_payload_ignores_unsupported_types() -> None:
    assert attachment_from_payload({"type": "sticker", "payload": {}}) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://iu.oneme.ru/file.png",
        "https://fu.oneme.ru/file.pdf",
        "https://vu.okcdn.ru/file.mp4",
        "https://cdn.max.ru/file.bin",
    ],
)
def test_official_media_hosts_are_allowed(url: str) -> None:
    assert is_allowed_media_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://iu.oneme.ru/file.png",
        "https://127.0.0.1/file.png",
        "https://iu.oneme.ru.evil.example/file.png",
        "https://evil.example/file.png",
        "not-a-url",
    ],
)
def test_media_url_guard_rejects_non_official_or_unsafe_hosts(url: str) -> None:
    assert is_allowed_media_url(url) is False


@pytest.mark.parametrize(
    ("path", "expected"),
    [("photo.png", "image"), ("clip.mp4", "video"), ("voice.ogg", "audio"), ("report.pdf", "file")],
)
def test_media_type_for_file(path: str, expected: str) -> None:
    assert media_type_for_file(path) == expected


def test_media_type_for_file_supports_voice_and_force_document() -> None:
    assert media_type_for_file("voice.bin", is_voice=True) == "audio"
    assert media_type_for_file("photo.png", force_document=True) == "file"


def test_mime_type_for_file_uses_extension_then_safe_fallback() -> None:
    assert mime_type_for_file("photo.png", "image") == "image/png"
    assert mime_type_for_file("unknown.max", "file") == "application/octet-stream"


@pytest.mark.asyncio
async def test_client_download_media_does_not_send_bot_token_to_cdn() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "image/png", "content-length": "8"},
            content=b"\x89PNG\r\n\x1a\n",
            request=request,
        )

    api_http = httpx.AsyncClient(
        base_url="https://platform-api2.max.ru",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}, request=request)),
    )
    media_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MaxClient(
        "secret-token",
        http_client=api_http,
        media_http_client=media_http,
    )

    data, mime_type = await client.download_media("https://iu.oneme.ru/file.png", max_bytes=16)
    await client.close()

    assert data.startswith(b"\x89PNG")
    assert mime_type == "image/png"
    assert seen
    assert "authorization" not in seen[0].headers


@pytest.mark.asyncio
async def test_client_upload_media_uses_current_upload_contract(tmp_path) -> None:
    uploaded = []

    def api_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/uploads"
        assert request.url.params["type"] == "file"
        return httpx.Response(
            200,
            json={"url": "https://fu.oneme.ru/upload.do?sig=opaque"},
            request=request,
        )

    def media_handler(request: httpx.Request) -> httpx.Response:
        uploaded.append(request)
        return httpx.Response(200, json={"token": "uploaded-token"}, request=request)

    path = tmp_path / "report.txt"
    path.write_text("hello", encoding="utf-8")
    api_http = httpx.AsyncClient(
        base_url="https://platform-api2.max.ru",
        transport=httpx.MockTransport(api_handler),
    )
    media_http = httpx.AsyncClient(transport=httpx.MockTransport(media_handler))
    client = MaxClient(
        "secret-token",
        http_client=api_http,
        media_http_client=media_http,
    )

    result = await client.upload_media(path, media_type="file", max_bytes=16)
    await client.close()

    assert result["token"] == "uploaded-token"
    assert uploaded
    assert b"report.txt" in uploaded[0].content
    assert b"hello" in uploaded[0].content
    assert uploaded[0].headers["authorization"] == "secret-token"


@pytest.mark.asyncio
async def test_adapter_caches_incoming_media_into_hermes_event(monkeypatch) -> None:
    class _Client:
        async def download_media(self, url: str, *, max_bytes: int):
            assert url == "https://iu.oneme.ru/photo.png"
            assert max_bytes == 1024
            return b"image-bytes", "image/png"

    cached = type(
        "Cached",
        (),
        {
            "path": "/home/user/.hermes/cache/images/max.png",
            "media_type": "image/png",
            "context_note": lambda self: "[image 'photo.png' saved at: /home/user/.hermes/cache/images/max.png]",
        },
    )()
    monkeypatch.setattr(max_adapter_module, "_cache_media_bytes", lambda *args, **kwargs: cached)

    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = _Client()
    adapter._media_max_bytes = 1024
    adapter.platform = "max"
    message = MaxMessage(
        message_id="mid-1",
        user_id="user-1",
        user_name="User",
        chat_id="user-1",
        chat_type="dialog",
        chat_title=None,
        text="Посмотри",
        attachments=(
            {
                "type": "image",
                "payload": {"url": "https://iu.oneme.ru/photo.png", "filename": "photo.png"},
            },
        ),
    )
    event = _build_message_event(adapter, message)

    await adapter._populate_message_media(message, event)

    assert event.media_urls == ["/home/user/.hermes/cache/images/max.png"]
    assert event.media_types == ["image/png"]
    assert "saved at" in event.text


@pytest.mark.asyncio
async def test_adapter_sends_media_tag_as_token_attachment(monkeypatch) -> None:
    class _Client:
        def __init__(self) -> None:
            self.uploads = []
            self.sent = []

        async def upload_media(self, path, *, media_type, max_bytes, mime_type):
            self.uploads.append((path, media_type, max_bytes, mime_type))
            return {"token": "file-token"}

        async def send_message(self, target_id, text, **kwargs):
            self.sent.append((target_id, text, kwargs))
            return {"message": {"body": {"mid": "media-mid"}}}

    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = _Client()
    adapter._chat_target_types = {"user-1": "user"}
    adapter._media_max_bytes = 1024
    adapter._rate_limiter = max_adapter_module.MaxRateLimiter()
    monkeypatch.setattr(
        adapter,
        "extract_media",
        lambda _content: ([("/tmp/report.pdf", False)], "Отчёт"),
        raising=False,
    )
    monkeypatch.setattr(
        adapter,
        "filter_media_delivery_paths",
        lambda media_files: media_files,
        raising=False,
    )

    result = await adapter.send("user-1", "MEDIA:/tmp/report.pdf\nОтчёт")

    assert result.success is True
    assert adapter._client.uploads == [
        ("/tmp/report.pdf", "file", 1024, "application/pdf")
    ]
    assert adapter._client.sent[0][1] == "Отчёт"
    assert adapter._client.sent[0][2]["attachments"] == [
        {"type": "file", "payload": {"token": "file-token"}}
    ]


@pytest.mark.asyncio
async def test_standalone_sender_delivers_media_files(monkeypatch, tmp_path) -> None:
    class _Client:
        sent = []

        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def upload_media(self, path, *, media_type, max_bytes, mime_type):
            assert media_type == "file"
            assert max_bytes == 1024
            assert mime_type == "application/pdf"
            return {"token": "cron-token"}

        async def send_message(self, target_id, text, **kwargs):
            self.sent.append((target_id, text, kwargs))
            return {"message": {"body": {"mid": "cron-mid"}}}

        async def close(self):
            return None

    monkeypatch.setattr(max_adapter_module, "MaxClient", _Client)
    config = SimpleNamespace(
        token="secret-token",
        extra={"target_path": str(tmp_path / "targets.sqlite3"), "media_max_bytes": 1024},
    )

    result = await standalone_send(
        config,
        "user-1",
        "Отчёт",
        media_files=["report.pdf"],
    )

    assert result["success"] is True
    assert _Client.sent[0][2]["attachments"] == [
        {"type": "file", "payload": {"token": "cron-token"}}
    ]
