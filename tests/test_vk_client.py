from __future__ import annotations

import json

import httpx
import pytest

from plugins.vk.client import VkApiClient, VkApiError


@pytest.mark.asyncio
async def test_vk_api_client_posts_credentials_without_leaking_token_to_url():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["form"] = dict(httpx.QueryParams(request.content.decode()))
        return httpx.Response(200, json={"response": {"server": "https://lp", "key": "k", "ts": "7"}})

    client = VkApiClient(
        token="secret-token",
        group_id=123,
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.get_long_poll_server()
    finally:
        await client.close()

    assert result["ts"] == "7"
    assert "secret-token" not in seen["url"]
    assert seen["form"]["access_token"] == "secret-token"
    assert seen["form"]["group_id"] == "123"


@pytest.mark.asyncio
async def test_vk_api_client_parses_vk_errors_and_marks_transient_codes():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"error": {"error_code": 9, "error_msg": "flood control"}},
        )

    client = VkApiClient("secret-token", 123, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(VkApiError) as exc_info:
            await client.call("messages.send", {"peer_id": 1, "message": "hi"})
    finally:
        await client.close()

    assert exc_info.value.code == 9
    assert exc_info.value.retryable is True
    assert "secret-token" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_long_poll_check_uses_marker_and_returns_events():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=json.dumps(
                {
                    "ts": "8",
                    "failed": 0,
                    "updates": [
                        {
                            "type": "message_new",
                            "object": {"message": {"id": 42, "peer_id": 100000001, "from_id": 100000001, "text": "hi"}},
                            "group_id": 123,
                        }
                    ],
                }
            ).encode(),
        )

    client = VkApiClient("secret-token", 123, transport=httpx.MockTransport(handler))
    try:
        result = await client.long_poll_check("https://lp", "key", "7", wait=1)
    finally:
        await client.close()

    assert result.ts == "8"
    assert result.failed == 0
    assert len(result.updates) == 1
    assert requests[0].url.params["ts"] == "7"
    assert "secret-token" not in str(requests[0].url)


@pytest.mark.asyncio
async def test_vk_send_message_posts_format_data_as_json_form_field():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["form"] = dict(httpx.QueryParams(request.content.decode()))
        return httpx.Response(200, json={"response": 7})

    client = VkApiClient("secret-token", 123, transport=httpx.MockTransport(handler))
    try:
        assert await client.send_message(
            100000001,
            "hello",
            random_id=1,
            format_data={"version": 1, "items": [{"type": "bold", "offset": 0, "length": 5}]},
        ) == 7
    finally:
        await client.close()

    assert '"version":1' in seen["form"]["format_data"]


@pytest.mark.asyncio
async def test_vk_audio_upload_uses_audio_message_document_type(tmp_path):
    path = tmp_path / "voice.ogg"
    path.write_bytes(b"voice")
    client = VkApiClient("secret-token", 123, transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    calls = []

    async def fake_call(method, params=None):
        calls.append((method, params or {}))
        if method == "docs.getMessagesUploadServer":
            return {"upload_url": "https://upload.example"}
        return {"doc": {"owner_id": 1, "id": 2}}

    async def fake_upload(*args, **kwargs):
        assert kwargs["field_name"] == "file"
        return {"file": "uploaded"}

    client.call = fake_call  # type: ignore[method-assign]
    client.upload_file = fake_upload  # type: ignore[method-assign]
    try:
        assert await client.upload_audio(100000001, str(path), max_bytes=1024) == "doc1_2"
    finally:
        await client.close()

    assert calls[0] == (
        "docs.getMessagesUploadServer",
        {"peer_id": 100000001, "type": "audio_message"},
    )


@pytest.mark.asyncio
async def test_vk_long_poll_http_error_keeps_status_code_for_reinitialization():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"failed": 3})

    client = VkApiClient("secret-token", 123, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(VkApiError) as exc_info:
            await client.long_poll_check("https://lp", "key", "7", wait=1)
    finally:
        await client.close()

    assert exc_info.value.code == 400
    assert exc_info.value.retryable is True
