from __future__ import annotations

import pytest
import httpx
from io import BytesIO
from types import SimpleNamespace
from PIL import Image

import plugins.max.adapter as max_adapter_module
from plugins.max.adapter import _build_message_event
from plugins.max.adapter import standalone_send
from plugins.max.client import DEFAULT_MEDIA_MAX_BYTES, MaxApiError, MaxClient
from plugins.max.rate_limit import MAX_ATTACHMENT_SIZE
from plugins.max.models import MaxMessage
from plugins.max.media import (
    MaxAttachment,
    attachment_from_payload,
    is_allowed_media_url,
    media_type_for_file,
    mime_type_for_file,
    validate_image_dimensions,
)


def _png_bytes(width: int = 1, height: int = 1) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height)).save(output, format="PNG")
    return output.getvalue()


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


def test_attachment_from_payload_keeps_nested_token_without_url() -> None:
    attachment = attachment_from_payload(
        {
            "type": "video",
            "payload": {"token": "video-token", "filename": "clip.mp4"},
        }
    )

    assert attachment is not None
    assert attachment.url is None
    assert attachment.token == "video-token"
    assert attachment.filename == "clip.mp4"


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


def test_max_media_limit_is_exactly_50_million_bytes() -> None:
    assert DEFAULT_MEDIA_MAX_BYTES == 50_000_000
    assert MAX_ATTACHMENT_SIZE == 50_000_000


def test_image_dimensions_enforce_max_side_length() -> None:
    assert validate_image_dimensions(_png_bytes(7680, 1)) == (7680, 1)
    with pytest.raises(ValueError, match="7680"):
        validate_image_dimensions(_png_bytes(7681, 1))


def test_heic_dimensions_when_heif_support_is_installed() -> None:
    pytest.importorskip("pillow_heif")
    output = BytesIO()
    Image.new("RGB", (320, 240)).save(output, format="HEIF")
    assert validate_image_dimensions(output.getvalue(), filename="sample.heic") == (320, 240)


@pytest.mark.asyncio
async def test_upload_rejects_file_over_default_limit_before_api_request(tmp_path) -> None:
    class _Client(MaxClient):
        def __init__(self) -> None:
            self.requested = False

        async def _request(self, *args, **kwargs):
            self.requested = True
            raise AssertionError("oversized file must be rejected before API request")

    path = tmp_path / "oversize.bin"
    with path.open("wb") as file_obj:
        file_obj.truncate(DEFAULT_MEDIA_MAX_BYTES + 1)

    client = _Client()
    with pytest.raises(MaxApiError, match="exceeds configured size limit"):
        await client.upload_media(
            path,
            media_type="file",
            max_bytes=DEFAULT_MEDIA_MAX_BYTES + 100,
        )
    assert client.requested is False


@pytest.mark.asyncio
async def test_upload_rejects_oversized_image_dimensions_before_api_request(tmp_path) -> None:
    class _Client(MaxClient):
        def __init__(self) -> None:
            self.requested = False

        async def _request(self, *args, **kwargs):
            self.requested = True
            raise AssertionError("invalid image must be rejected before API request")

    path = tmp_path / "wide.png"
    path.write_bytes(_png_bytes(7681, 1))
    client = _Client()

    with pytest.raises(MaxApiError, match="dimensions"):
        await client.upload_media(path, media_type="image")
    assert client.requested is False


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
    assert "authorization" not in uploaded[0].headers


@pytest.mark.asyncio
async def test_adapter_caches_incoming_media_into_hermes_event(monkeypatch) -> None:
    class _Client:
        async def download_media(self, url: str, *, max_bytes: int):
            assert url == "https://iu.oneme.ru/photo.png"
            assert max_bytes == 1024
            return _png_bytes(), "image/png"

    cached = type(
        "Cached",
        (),
        {
            "path": "/home/xidden/.hermes/cache/images/max.png",
            "media_type": "image/png",
            "context_note": lambda self: "[image 'photo.png' saved at: /home/xidden/.hermes/cache/images/max.png]",
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

    assert event.media_urls == ["/home/xidden/.hermes/cache/images/max.png"]
    assert event.media_types == ["image/png"]
    assert "saved at" in event.text


@pytest.mark.asyncio
async def test_adapter_rejects_inbound_image_over_dimension_limit(monkeypatch) -> None:
    class _Client:
        async def download_media(self, url: str, *, max_bytes: int):
            return _png_bytes(7681, 1), "image/png"

    cached = False

    def cache_media(*args, **kwargs):
        nonlocal cached
        cached = True
        raise AssertionError("oversized image must not enter Hermes cache")

    monkeypatch.setattr(max_adapter_module, "_cache_media_bytes", cache_media)
    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = _Client()
    adapter._media_max_bytes = 1024
    adapter.platform = "max"
    message = MaxMessage(
        message_id="mid-wide-image",
        user_id="user-1",
        user_name="User",
        chat_id="user-1",
        chat_type="dialog",
        chat_title=None,
        text="Посмотри",
        attachments=(
            {
                "type": "image",
                "payload": {"url": "https://iu.oneme.ru/wide.png", "filename": "wide.png"},
            },
        ),
    )
    event = _build_message_event(adapter, message)

    await adapter._populate_message_media(message, event)

    assert cached is False
    assert event.media_urls == []
    assert "не удалось скачать" in event.text


@pytest.mark.asyncio
async def test_unsupported_audio_is_sent_unchanged_as_document(tmp_path) -> None:
    class _Client:
        def __init__(self) -> None:
            self.upload_types = []
            self.messages = []

        async def upload_media(self, path, *, media_type, **kwargs):
            self.upload_types.append(media_type)
            if media_type == "audio":
                raise MaxApiError(
                    "Audio upload rejected",
                    code="file_extension_forbidden",
                    status_code=400,
                )
            return {"token": "document-token"}

        async def send_message(self, target_id, text, **kwargs):
            self.messages.append((target_id, text, kwargs))
            return {"message": {"body": {"mid": "document-mid"}}}

    audio_path = tmp_path / "original.wav"
    audio_path.write_bytes(b"original audio bytes")
    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = _Client()
    adapter._media_max_bytes = 1024
    adapter._chat_target_types = {"user-1": "user"}
    adapter._rate_limiter = max_adapter_module.MaxRateLimiter()
    adapter.validate_media_delivery_path = lambda path: str(path)

    result = await adapter.send_voice("user-1", str(audio_path), caption="Речь")

    assert result.success is True
    assert adapter._client.upload_types == ["audio", "file"]
    sent = adapter._client.messages[0]
    assert sent[2]["attachments"] == [{"type": "file", "payload": {"token": "document-token"}}]
    assert sent[1] == "Речь\n\nАудиоформат не поддержан MAX; исходный файл отправлен как документ."


@pytest.mark.asyncio
async def test_audio_send_rejection_falls_back_to_document_without_losing_caption(tmp_path) -> None:
    class _Client:
        def __init__(self) -> None:
            self.upload_types = []
            self.attempts = []

        async def upload_media(self, path, *, media_type, **kwargs):
            del path, kwargs
            self.upload_types.append(media_type)
            return {"token": f"{media_type}-token"}

        async def send_message(self, target_id, text, **kwargs):
            self.attempts.append((target_id, text, kwargs))
            attachments = kwargs.get("attachments", [])
            if attachments and attachments[0]["type"] == "audio":
                raise MaxApiError(
                    "Audio format rejected",
                    code="unsupported_audio_format",
                    status_code=415,
                )
            return {"message": {"body": {"mid": f"mid-{len(self.attempts)}"}}}

    audio_path = tmp_path / "recording.wav"
    audio_path.write_bytes(b"original audio bytes")
    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = _Client()
    adapter._media_max_bytes = 1024
    adapter._chat_target_types = {"user-1": "user"}
    adapter._rate_limiter = max_adapter_module.MaxRateLimiter()
    adapter.validate_media_delivery_path = lambda path: str(path)
    caption = "Проверка " + ("длинной подписи " * 300)

    result = await adapter.send_voice("user-1", str(audio_path), caption=caption)

    assert result.success is True
    assert adapter._client.upload_types == ["audio", "file"]
    accepted = [
        call for call in adapter._client.attempts
        if not call[2].get("attachments")
        or call[2]["attachments"][0]["type"] == "file"
    ]
    assert accepted[0][2]["attachments"] == [
        {"type": "file", "payload": {"token": "file-token"}}
    ]
    assert "Проверка" in "".join(call[1] for call in accepted)
    assert "Аудиоформат не поддержан MAX" in "".join(call[1] for call in accepted)
    assert "Аудиоформат не поддержан MAX" not in adapter._client.attempts[1][1]


@pytest.mark.parametrize("reject_at", ["upload", "send"])
@pytest.mark.asyncio
async def test_standalone_audio_falls_back_to_document(monkeypatch, tmp_path, reject_at) -> None:
    class _Client:
        instances = []

        def __init__(self, *args, **kwargs):
            del args, kwargs
            self.upload_types = []
            self.attempts = []
            self.__class__.instances.append(self)

        async def upload_media(self, path, *, media_type, **kwargs):
            del path, kwargs
            self.upload_types.append(media_type)
            if reject_at == "upload" and media_type == "audio":
                raise MaxApiError(
                    "Audio format rejected",
                    code="file_extension_forbidden",
                    status_code=400,
                )
            return {"token": f"{media_type}-token"}

        async def send_message(self, target_id, text, **kwargs):
            self.attempts.append((target_id, text, kwargs))
            attachments = kwargs.get("attachments", [])
            if reject_at == "send" and attachments and attachments[0]["type"] == "audio":
                raise MaxApiError(
                    "Audio format rejected",
                    code="unsupported_audio_format",
                    status_code=415,
                )
            return {"message": {"body": {"mid": f"cron-{len(self.attempts)}"}}}

        async def close(self):
            return None

    monkeypatch.setattr(max_adapter_module, "MaxClient", _Client)
    config = SimpleNamespace(
        token="test-token",
        extra={"target_path": str(tmp_path / "targets.sqlite3"), "media_max_bytes": 1024},
    )
    audio_path = tmp_path / "voice.ogg"
    audio_path.write_bytes(b"original audio bytes")

    result = await standalone_send(
        config,
        "user-1",
        "Голосовое сообщение",
        media_files=[(str(audio_path), True)],
    )

    assert result["success"] is True
    client = _Client.instances[0]
    assert client.upload_types == ["audio", "file"]
    document_send = next(
        call for call in client.attempts
        if call[2].get("attachments", [{}])[0].get("type") == "file"
    )
    assert document_send[2]["attachments"] == [
        {"type": "file", "payload": {"token": "file-token"}}
    ]
    assert any(
        "Аудиоформат не поддержан MAX" in call[1]
        for call in client.attempts
    )


@pytest.mark.asyncio
async def test_adapter_resolves_inbound_video_token_before_download(monkeypatch) -> None:
    class _Client:
        async def get_video(self, token: str):
            assert token == "video-token"
            return {"urls": {"mp4": "https://vu.okcdn.ru/video.mp4"}}

        async def download_media(self, url: str, *, max_bytes: int):
            assert url == "https://vu.okcdn.ru/video.mp4"
            return b"video-bytes", "video/mp4"

    cached = type(
        "Cached",
        (),
        {
            "path": "/home/xidden/.hermes/cache/videos/max.mp4",
            "media_type": "video/mp4",
            "context_note": lambda self: "[video saved at: /home/xidden/.hermes/cache/videos/max.mp4]",
        },
    )()
    monkeypatch.setattr(max_adapter_module, "_cache_media_bytes", lambda *args, **kwargs: cached)

    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = _Client()
    adapter.platform = "max"
    adapter._media_max_bytes = 1024
    message = MaxMessage(
        message_id="mid-video",
        user_id="user-1",
        user_name="User",
        chat_id="user-1",
        chat_type="dialog",
        chat_title=None,
        text="Видео",
        attachments=({"type": "video", "payload": {"token": "video-token"}},),
    )
    event = _build_message_event(adapter, message)

    await adapter._populate_message_media(message, event)

    assert event.media_urls == ["/home/xidden/.hermes/cache/videos/max.mp4"]
    assert event.media_types == ["video/mp4"]


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
async def test_group_session_id_is_translated_back_to_real_max_chat() -> None:
    class _Client:
        def __init__(self) -> None:
            self.sent = []

        async def send_message(self, target_id, text, **kwargs):
            self.sent.append((target_id, text, kwargs))
            return {"message": {"body": {"mid": "group-mid"}}}

    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = _Client()
    adapter._chat_target_types = {"group-1": "chat"}
    adapter._rate_limiter = max_adapter_module.MaxRateLimiter()

    result = await adapter.send("group-1::user::member-1", "hello")

    assert result.success is True
    assert adapter._client.sent[0][0] == "group-1"
    assert adapter._client.sent[0][2]["target_type"] == "chat"


@pytest.mark.asyncio
async def test_typing_actions_use_physical_chat_id_for_group_session() -> None:
    class _Client:
        def __init__(self) -> None:
            self.actions = []

        async def send_action(self, chat_id, action):
            self.actions.append((chat_id, action))
            return {"success": True}

    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = _Client()
    adapter._rate_limiter = max_adapter_module.MaxRateLimiter()

    await adapter.send_typing("group-1::user::member-1")
    await adapter.stop_typing("group-1::user::member-1")

    assert adapter._client.actions == [("group-1", "typing"), ("group-1", "typing_off")]


@pytest.mark.asyncio
async def test_media_batch_delivers_successful_files_and_reports_partial_failure(monkeypatch) -> None:
    class _Client:
        def __init__(self) -> None:
            self.sent = []

        async def upload_media(self, path, *, media_type, max_bytes, mime_type):
            if str(path).endswith("bad.pdf"):
                raise max_adapter_module.MaxApiError("bad attachment")
            return {"token": "good-token"}

        async def send_message(self, target_id, text, **kwargs):
            self.sent.append((target_id, text, kwargs))
            return {"message": {"body": {"mid": "partial-mid"}}}

    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = _Client()
    adapter._chat_target_types = {"user-1": "user"}
    adapter._media_max_bytes = 1024
    adapter._rate_limiter = max_adapter_module.MaxRateLimiter()
    monkeypatch.setattr(
        adapter,
        "extract_media",
        lambda _content: ([
            ("/tmp/bad.pdf", False),
            ("/tmp/good.pdf", False),
        ], "files"),
        raising=False,
    )
    monkeypatch.setattr(adapter, "filter_media_delivery_paths", lambda media_files: media_files, raising=False)

    result = await adapter.send("user-1", "MEDIA:/tmp/bad.pdf MEDIA:/tmp/good.pdf")

    assert result.success is False
    assert result.error_kind == "partial_media"
    assert adapter._client.sent[0][2]["attachments"] == [
        {"type": "file", "payload": {"token": "good-token"}}
    ]


@pytest.mark.asyncio
async def test_media_batch_splits_file_from_image_video(monkeypatch) -> None:
    class _Client:
        def __init__(self) -> None:
            self.sent = []

        async def upload_media(self, path, *, media_type, max_bytes, mime_type):
            del path, max_bytes, mime_type
            return {"token": f"{media_type}-token"}

        async def send_message(self, target_id, text, **kwargs):
            self.sent.append((target_id, text, kwargs))
            mid = f"mid-{len(self.sent)}"
            return {"message": {"body": {"mid": mid}}}

    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = _Client()
    adapter._chat_target_types = {"user-1": "user"}
    adapter._media_max_bytes = 1024
    adapter._rate_limiter = max_adapter_module.MaxRateLimiter()

    result = await adapter._send_media_files(
        "user-1",
        "mixed media",
        [
            ("photo.png", False),
            ("clip.mp4", False),
            ("report.pdf", False),
        ],
    )

    assert result.success is True
    assert len(adapter._client.sent) == 2
    assert [item["type"] for item in adapter._client.sent[0][2]["attachments"]] == [
        "image",
        "video",
    ]
    assert adapter._client.sent[1][1] == ""
    assert adapter._client.sent[1][2]["attachments"] == [
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
    report = tmp_path / "report.pdf"
    report.write_bytes(b"%PDF-test")

    result = await standalone_send(
        config,
        "user-1",
        "Отчёт",
        media_files=[str(report)],
    )

    assert result["success"] is True
    assert _Client.sent[0][2]["attachments"] == [
        {"type": "file", "payload": {"token": "cron-token"}}
    ]


@pytest.mark.asyncio
async def test_standalone_sender_delivers_text_without_media(monkeypatch, tmp_path) -> None:
    class _Client:
        sent = []

        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def send_message(self, target_id, text, **kwargs):
            self.sent.append((target_id, text, kwargs))
            return {"message": {"body": {"mid": "cron-text-mid"}}}

        async def close(self):
            return None

    monkeypatch.setattr(max_adapter_module, "MaxClient", _Client)
    config = SimpleNamespace(token="secret-token", extra={"target_path": str(tmp_path / "targets.sqlite3")})

    result = await standalone_send(config, "user-1", "Плановое уведомление")

    assert result["success"] is True
    assert _Client.sent == [("user-1", "Плановое уведомление", {"target_type": "user"})]


@pytest.mark.asyncio
async def test_send_multiple_images_returns_aggregate_send_result() -> None:
    from plugins.max.adapter import SendResult

    adapter = object.__new__(max_adapter_module.MaxAdapter)
    calls = []

    class _Client:
        def __init__(self) -> None:
            self.sent = []

        async def send_message(self, chat_id, text, **kwargs):
            self.sent.append((chat_id, text, kwargs))
            return {"message": {"body": {"mid": "notice-mid"}}}

    adapter._client = _Client()
    adapter._chat_target_types = {"user-1": "user"}
    adapter._rate_limiter = max_adapter_module.MaxRateLimiter()

    async def send_image(chat_id, image_url, **kwargs):
        calls.append(image_url)
        if image_url.endswith("failed.png"):
            return SendResult(success=False, error="upload failed")
        return SendResult(success=True, message_id=f"mid-{len(calls)}")

    adapter.send_image = send_image

    result = await adapter.send_multiple_images(
        "user-1",
        [
            ("https://img.example/one.png", "one"),
            ("https://img.example/failed.png", "bad"),
            ("https://img.example/two.png", "two"),
        ],
    )

    assert result.success is True
    assert result.message_id == "mid-3"
    assert result.continuation_message_ids == ("mid-1",)
    assert result.raw_response["failed_images"][0]["index"] == 2
    assert adapter._client.sent[0][1] == "Не удалось доставить изображения №2. Остальные изображения отправлены."


@pytest.mark.asyncio
async def test_send_multiple_images_continues_after_unexpected_error() -> None:
    from plugins.max.adapter import SendResult

    adapter = object.__new__(max_adapter_module.MaxAdapter)
    calls = 0

    async def send_image(chat_id, image_url, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic error")
        return SendResult(success=True, message_id="delivered")

    adapter.send_image = send_image
    result = await adapter.send_multiple_images(
        "user-1", [("https://img.example/fail.png", ""), ("https://img.example/ok.png", "")]
    )

    assert result.success is True
    assert result.message_id == "delivered"
    assert result.error_kind == "partial_media"
    assert "synthetic error" in result.error


@pytest.mark.asyncio
async def test_send_multiple_images_notifies_user_when_every_image_fails() -> None:
    from plugins.max.adapter import SendResult

    class _Client:
        def __init__(self) -> None:
            self.sent = []

        async def send_message(self, chat_id, text, **kwargs):
            self.sent.append((chat_id, text, kwargs))
            return {"message": {"body": {"mid": "notice-mid"}}}

    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = _Client()
    adapter._chat_target_types = {"user-1": "user"}
    adapter._rate_limiter = max_adapter_module.MaxRateLimiter()

    async def fail_send_image(*args, **kwargs):
        return SendResult(success=False, error="upload failed")

    adapter.send_image = fail_send_image
    result = await adapter.send_multiple_images(
        "user-1", [("https://img.example/a.png", ""), ("https://img.example/b.png", "")]
    )

    assert result.success is False
    assert adapter._client.sent[0][1] == "Не удалось доставить изображения №1, №2."


@pytest.mark.asyncio
async def test_local_media_caption_is_split_without_truncation(tmp_path) -> None:
    media_path = tmp_path / "image.png"
    media_path.write_bytes(b"image")

    class _Client:
        def __init__(self):
            self.sent = []

        async def upload_media(self, path, **kwargs):
            return {"token": "image-token"}

        async def send_message(self, target_id, text, **kwargs):
            self.sent.append(text)
            return {"message": {"body": {"mid": f"mid-{len(self.sent)}"}}}

    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = _Client()
    adapter._chat_target_types = {"user-1": "user"}
    adapter._media_max_bytes = 1024
    adapter._rate_limiter = max_adapter_module.MaxRateLimiter()
    adapter.validate_media_delivery_path = lambda path: str(path)
    caption = "я" * 4501

    result = await adapter.send_image_file("user-1", str(media_path), caption=caption)

    assert result.success is True
    assert adapter._client.sent == ["я" * 4000, "я" * 501]
    assert result.message_id == "mid-2"
    assert result.continuation_message_ids == ("mid-1",)


@pytest.mark.asyncio
async def test_adapter_direct_hermes_media_hooks_use_native_attachments(monkeypatch, tmp_path) -> None:
    class _Client:
        def __init__(self) -> None:
            self.uploads = []
            self.sent = []

        async def upload_media(self, path, *, media_type, max_bytes, mime_type, **kwargs):
            self.uploads.append((str(path), media_type, max_bytes, mime_type))
            return {"token": f"{media_type}-token"}

        async def send_message(self, target_id, text, **kwargs):
            self.sent.append((target_id, text, kwargs))
            return {"message": {"body": {"mid": f"mid-{len(self.sent)}"}}}

    files = {
        "image": tmp_path / "картина.png",
        "file": tmp_path / "отчет.pdf",
        "audio": tmp_path / "голос.ogg",
        "video": tmp_path / "ролик.mp4",
    }
    for path in files.values():
        path.write_bytes(b"media")
    client = _Client()
    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = client
    adapter._chat_target_types = {"user-1": "user"}
    adapter._media_max_bytes = 1024
    adapter._rate_limiter = max_adapter_module.MaxRateLimiter()
    monkeypatch.setattr(adapter, "validate_media_delivery_path", lambda path: path)

    assert (await adapter.send_image_file("user-1", str(files["image"]))).success is True
    assert (await adapter.send_document("user-1", str(files["file"]), file_name="отчет.pdf")).success is True
    assert (await adapter.send_voice("user-1", str(files["audio"]))).success is True
    assert (await adapter.send_video("user-1", str(files["video"]))).success is True

    assert [item[1] for item in client.uploads] == ["image", "file", "audio", "video"]
    assert all(item[2]["attachments"][0]["payload"]["token"].endswith("-token") for item in client.sent)


@pytest.mark.asyncio
async def test_adapter_edit_message_keeps_chat_id_for_rate_limiter() -> None:
    class _Client:
        async def edit_message(self, message_id, text):
            assert message_id == "mid-1"
            assert text == "updated"
            return {"success": True}

    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = _Client()
    adapter._rate_limiter = max_adapter_module.MaxRateLimiter()

    result = await adapter.edit_message("user-1", "mid-1", "updated")

    assert result.success is True
    assert result.message_id == "mid-1"


@pytest.mark.asyncio
async def test_adapter_remote_image_is_reuploaded_through_max_upload_flow() -> None:
    class _Client:
        def __init__(self) -> None:
            self.uploads = []
            self.sent = []

        async def download_media(self, url, *, max_bytes, allowed_hosts):
            assert url == "https://fal.media/result.png"
            assert max_bytes == 1024
            assert "fal.media" in allowed_hosts
            return b"png-bytes", "image/png"

        async def upload_media_bytes(self, data, *, filename, media_type, max_bytes, mime_type):
            self.uploads.append((data, filename, media_type, max_bytes, mime_type))
            return {"token": "remote-image-token"}

        async def send_message(self, target_id, text, **kwargs):
            self.sent.append((target_id, text, kwargs))
            return {"message": {"body": {"mid": "remote-mid"}}}

    client = _Client()
    adapter = object.__new__(max_adapter_module.MaxAdapter)
    adapter._client = client
    adapter._chat_target_types = {"user-1": "user"}
    adapter._media_max_bytes = 1024
    adapter._rate_limiter = max_adapter_module.MaxRateLimiter()

    result = await adapter.send_image("user-1", "https://fal.media/result.png", caption="Готово")

    assert result.success is True
    assert client.uploads == [(b"png-bytes", "result.png", "image", 1024, "image/png")]
    assert client.sent[0][2]["attachments"] == [
        {"type": "image", "payload": {"token": "remote-image-token"}}
    ]
