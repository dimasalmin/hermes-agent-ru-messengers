from __future__ import annotations

import json

import pytest

from plugins.max.webhook_app import MaxWebhookASGI, MaxWebhookIngress
from plugins.max.webhook import WebhookResult


async def _call(app, *, method="POST", body=b"{}", headers=None):
    sent = []
    received = False
    headers = headers or []

    async def receive():
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    await app(
        {
            "type": "http",
            "method": method,
            "path": "/webhooks/max",
            "headers": headers,
        },
        receive,
        send,
    )
    return sent


@pytest.mark.asyncio
async def test_asgi_app_returns_receiver_status_and_payload() -> None:
    class Adapter:
        async def handle_webhook(self, headers, update):
            assert headers["X-Max-Bot-Api-Secret"] == "secret"
            assert update == {"update_type": "bot_started"}
            return WebhookResult(status_code=200, accepted=True)

    sent = await _call(
        MaxWebhookASGI(Adapter()),
        body=json.dumps({"update_type": "bot_started"}).encode(),
        headers=[(b"x-max-bot-api-secret", b"secret")],
    )

    assert sent[0]["status"] == 200
    assert json.loads(sent[1]["body"]) == {"accepted": True, "duplicate": False}


@pytest.mark.asyncio
async def test_asgi_app_rejects_invalid_json() -> None:
    class Adapter:
        async def handle_webhook(self, headers, update):
            raise AssertionError("invalid JSON must not reach adapter")

    sent = await _call(MaxWebhookASGI(Adapter()), body=b"not-json")

    assert sent[0]["status"] == 400


@pytest.mark.asyncio
async def test_asgi_app_rejects_unsupported_method() -> None:
    class Adapter:
        async def handle_webhook(self, headers, update):
            raise AssertionError("GET must not reach adapter")

    sent = await _call(MaxWebhookASGI(Adapter()), method="GET")

    assert sent[0]["status"] == 405


@pytest.mark.asyncio
async def test_ingress_delegates_to_durable_receiver() -> None:
    class Receiver:
        async def receive(self, headers, update):
            assert headers["X-Max-Bot-Api-Secret"] == "secret"
            assert update == {"update_type": "bot_started"}
            return WebhookResult(status_code=200, accepted=True, durable=True)

    sent = await _call(
        MaxWebhookASGI(MaxWebhookIngress(Receiver())),
        body=b'{"update_type":"bot_started"}',
        headers=[(b"x-max-bot-api-secret", b"secret")],
    )

    assert sent[0]["status"] == 200
