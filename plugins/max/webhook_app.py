"""Minimal ASGI ingress for MAX Webhook updates.

The app is framework-neutral and can be mounted behind a trusted HTTPS
reverse proxy. TLS termination and public port 443 remain deployment concerns.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from .webhook import WebhookResult


class MaxWebhookIngress:
    """Small handler used by a standalone ingress process."""

    def __init__(self, receiver: Any) -> None:
        self._receiver = receiver

    async def handle_webhook(
        self, headers: Mapping[str, str], update: Mapping[str, Any]
    ) -> WebhookResult:
        return await self._receiver.receive(headers, update)


class MaxWebhookASGI:
    """ASGI 3 application that ACKs through ``MaxAdapter.handle_webhook``."""

    def __init__(self, adapter: Any, *, max_body_bytes: int = 1_048_576) -> None:
        self._adapter = adapter
        self._max_body_bytes = max_body_bytes

    async def __call__(self, scope: Mapping[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            return
        if scope.get("method", "").upper() != "POST":
            await self._respond(send, 405, {"error": "method_not_allowed"})
            return

        body = bytearray()
        while True:
            event = await receive()
            if event.get("type") == "http.disconnect":
                return
            if event.get("type") != "http.request":
                continue
            body.extend(event.get("body", b""))
            if len(body) > self._max_body_bytes:
                await self._respond(send, 413, {"error": "request_too_large"})
                return
            if not event.get("more_body", False):
                break

        try:
            update = json.loads(bytes(body).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            await self._respond(send, 400, {"error": "invalid_json"})
            return
        if not isinstance(update, Mapping):
            await self._respond(send, 400, {"error": "update_must_be_object"})
            return

        headers = {}
        for key, value in scope.get("headers", []):
            decoded_key = key.decode("latin-1")
            decoded_value = value.decode("latin-1")
            if decoded_key.lower() == "x-max-bot-api-secret":
                decoded_key = "X-Max-Bot-Api-Secret"
            headers[decoded_key] = decoded_value
        result = await self._adapter.handle_webhook(headers, update)
        if not isinstance(result, WebhookResult):
            result = WebhookResult(status_code=500, accepted=False)
        await self._respond(
            send,
            result.status_code,
            {"accepted": result.accepted, "duplicate": result.duplicate},
        )

    @staticmethod
    async def _respond(send: Any, status: int, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
