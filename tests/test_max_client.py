from __future__ import annotations

import httpx
import pytest
from json import loads

from plugins.max.client import DEFAULT_API_BASE, MaxApiError, MaxClient


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
