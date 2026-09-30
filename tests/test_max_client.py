from __future__ import annotations

import httpx
import pytest
from json import loads
from PIL import Image

from plugins.max.client import DEFAULT_API_BASE, MaxApiError, MaxClient


def _write_png(path) -> None:
    image = Image.new("RGB", (1, 1))
    image.save(path, format="PNG")


def _client(handler):
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport, base_url=DEFAULT_API_BASE)
    return MaxClient("secret-token", http_client=http_client)


@pytest.mark.asyncio
async def test_send_message_uses_v2_host_authorization_and_user_query() -> None:
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, json={"message": {"body": {"mid": "out-1"}}})

    client = _client(handler)
    try:
        response = await client.send_message("42", "hello", link={"type": "reply", "message": "in-1"})
    finally:
        await client.close()

    request = seen["request"]
    assert str(request.url) == f"{DEFAULT_API_BASE}/messages?user_id=42"
    assert request.headers["Authorization"] == "secret-token"
    assert "secret-token" not in str(request.url)
    assert loads(request.content) == {
        "text": "hello",
        "link": {"type": "reply", "message": "in-1"},
        "notify": True,
        "format": "markdown",
    }
    assert response["message"]["body"]["mid"] == "out-1"


@pytest.mark.asyncio
async def test_edit_message_uses_message_id_query() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert str(request.url) == f"{DEFAULT_API_BASE}/messages?message_id=mid-1"
        assert loads(request.content) == {"text": "updated", "notify": True, "format": "markdown"}
        return httpx.Response(200, json={"success": True})

    client = _client(handler)
    try:
        result = await client.edit_message("mid-1", "updated")
    finally:
        await client.close()

    assert result == {"success": True}


@pytest.mark.asyncio
async def test_answer_callback_posts_message_update_by_callback_id() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == f"{DEFAULT_API_BASE}/answers?callback_id=cb-1"
        assert loads(request.content) == {
            "message": {
                "text": "Resolved",
                "attachments": [],
                "format": "markdown",
            }
        }
        return httpx.Response(200, json={"success": True})

    client = _client(handler)
    try:
        result = await client.answer_callback(
            "cb-1",
            message={"text": "Resolved", "attachments": [], "format": "markdown"},
        )
    finally:
        await client.close()

    assert result == {"success": True}


@pytest.mark.asyncio
async def test_subscribe_webhook_sends_secret_and_update_types() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/subscriptions"
        assert loads(request.content) == {
            "url": "https://example.test/max",
            "update_types": ["message_created", "message_callback"],
            "secret": "webhook-secret",
        }
        return httpx.Response(200, json={"success": True})

    client = _client(handler)
    try:
        result = await client.subscribe_webhook(
            "https://example.test/max",
            "webhook-secret",
            update_types=["message_created", "message_callback"],
        )
    finally:
        await client.close()

    assert result["success"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/max",
        "https://example.test:8443/max",
        "https://user:pass@example.test/max",
        "https://127.0.0.1/max",
    ],
)
async def test_subscribe_webhook_rejects_nonpublic_or_non443_urls(url: str) -> None:
    client = _client(lambda request: httpx.Response(500, request=request))
    try:
        with pytest.raises(ValueError):
            await client.subscribe_webhook(url, "webhook-secret")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_updates_preserves_marker_and_timeout() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/updates"
        assert request.url.params["marker"] == "123"
        assert request.url.params["timeout"] == "30"
        return httpx.Response(200, json={"updates": [], "marker": 124})

    client = _client(handler)
    try:
        result = await client.get_updates(marker=123, timeout=30)
    finally:
        await client.close()

    assert result["marker"] == 124


@pytest.mark.asyncio
async def test_commands_actions_and_video_resolution_use_documented_routes() -> None:
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "PATCH":
            assert request.url.path == "/me/commands"
            assert loads(request.content) == {
                "commands": [{"name": "menu", "description": "Menu"}]
            }
            return httpx.Response(200, json={"success": True})
        if request.method == "POST":
            assert request.url.path == "/chats/chat-1/actions"
            assert loads(request.content) == {"action": "typing"}
            return httpx.Response(200, json={"success": True})
        assert request.method == "GET"
        assert request.url.path == "/videos/video-token"
        return httpx.Response(200, json={"urls": {"mp4": "https://vu.okcdn.ru/video.mp4"}})

    client = _client(handler)
    try:
        assert await client.set_bot_commands([{"name": "menu", "description": "Menu"}]) == {"success": True}
        assert await client.send_action("chat-1", "typing") == {"success": True}
        video = await client.get_video("video-token")
    finally:
        await client.close()

    assert video["urls"]["mp4"].endswith("video.mp4")
    assert len(seen) == 3


@pytest.mark.asyncio
async def test_upload_uses_nested_photos_token_only_after_success(tmp_path) -> None:
    path = tmp_path / "photo.png"
    _write_png(path)

    async def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"url": "https://iu.oneme.ru/upload", "photos": {"token": "initial-photo-token"}},
            request=request,
        )

    async def media_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"photos": {"token": "nested-photo-token"}}, request=request)

    api_http = httpx.AsyncClient(
        base_url=DEFAULT_API_BASE,
        transport=httpx.MockTransport(api_handler),
    )
    media_http = httpx.AsyncClient(transport=httpx.MockTransport(media_handler))
    client = MaxClient("secret-token", http_client=api_http, media_http_client=media_http)
    try:
        result = await client.upload_media(path, media_type="image", max_bytes=256)
    finally:
        await client.close()

    assert result["token"] == "nested-photo-token"


@pytest.mark.asyncio
async def test_upload_extracts_token_from_live_photos_id_map(tmp_path) -> None:
    path = tmp_path / "photo.png"
    _write_png(path)

    async def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"url": "https://iu.oneme.ru/upload"},
            request=request,
        )

    async def media_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"photos": {"opaque-photo-id": {"token": "live-photo-token"}}},
            request=request,
        )

    api_http = httpx.AsyncClient(
        base_url=DEFAULT_API_BASE,
        transport=httpx.MockTransport(api_handler),
    )
    media_http = httpx.AsyncClient(transport=httpx.MockTransport(media_handler))
    client = MaxClient("secret-token", http_client=api_http, media_http_client=media_http)
    try:
        result = await client.upload_media(path, media_type="image", max_bytes=256)
    finally:
        await client.close()

    assert result["token"] == "live-photo-token"


@pytest.mark.asyncio
async def test_upload_does_not_use_initial_token_after_failed_multipart(tmp_path) -> None:
    path = tmp_path / "photo.png"
    _write_png(path)

    async def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"url": "https://iu.oneme.ru/upload", "token": "initial-photo-token"},
            request=request,
        )

    async def media_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "failed"}, request=request)

    api_http = httpx.AsyncClient(
        base_url=DEFAULT_API_BASE,
        transport=httpx.MockTransport(api_handler),
    )
    media_http = httpx.AsyncClient(transport=httpx.MockTransport(media_handler))
    client = MaxClient("secret-token", http_client=api_http, media_http_client=media_http)
    try:
        with pytest.raises(MaxApiError):
            await client.upload_media(path, media_type="image", max_bytes=256)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_api_error_exposes_retry_after_without_leaking_token() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"Retry-After": "2.5"},
            json={"code": "too_many_requests", "message": "slow down"},
        )

    client = _client(handler)
    try:
        with pytest.raises(MaxApiError) as error:
            await client.get_me()
    finally:
        await client.close()

    assert error.value.status_code == 429
    assert error.value.retry_after == 2.5
    assert "secret-token" not in str(error.value)


@pytest.mark.asyncio
async def test_attachment_not_ready_message_is_retryable() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"message": "Key: errors.process.attachment.file.not.processed"},
        )

    client = _client(handler)
    try:
        with pytest.raises(MaxApiError) as error:
            await client.send_message("42", "document", attachments=[])
    finally:
        await client.close()

    assert error.value.code == "attachment.not.ready"
