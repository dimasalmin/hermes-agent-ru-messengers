from __future__ import annotations

import httpx
import pytest

from plugins.vk.media import download_attachment


@pytest.mark.asyncio
async def test_vk_media_download_is_bounded_and_host_checked():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"image")

    attachment = {"type": "photo", "photo": {"sizes": [{"url": "https://sun9-1.userapi.com/a.jpg", "width": 10, "height": 10}]}}
    result = await download_attachment(
        attachment,
        max_bytes=10,
        transport=httpx.MockTransport(handler),
    )
    assert result is not None
    assert result.media_type == "photo"
    assert result.data == b"image"


@pytest.mark.asyncio
async def test_vk_media_rejects_non_vk_hosts_and_redirects():
    external = {"type": "doc", "doc": {"url": "https://example.com/file.bin", "title": "file.bin"}}
    assert await download_attachment(external, transport=httpx.MockTransport(lambda _request: httpx.Response(200))) is None

    async def redirect(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://vk.com/file"})

    vk = {"type": "doc", "doc": {"url": "https://vk.com/file.bin", "title": "file.bin"}}
    assert await download_attachment(vk, transport=httpx.MockTransport(redirect)) is None


@pytest.mark.asyncio
async def test_vk_media_rejects_body_larger_than_limit():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"0123456789")

    photo = {"type": "photo", "photo": {"sizes": [{"url": "https://vk.com/a.jpg"}]}}
    with pytest.raises(ValueError, match="size limit"):
        await download_attachment(photo, max_bytes=5, transport=httpx.MockTransport(handler))
