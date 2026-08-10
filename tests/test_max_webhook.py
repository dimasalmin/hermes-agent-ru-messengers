from __future__ import annotations

import pytest

from plugins.max.webhook import MaxWebhookReceiver


@pytest.mark.asyncio
async def test_receiver_accepts_valid_secret_and_deduplicates_update() -> None:
    receiver = MaxWebhookReceiver("secret-123")
    update = {"update_type": "message_created", "message": {"body": {"mid": "mid-1"}}}

    first = await receiver.receive({"X-Max-Bot-Api-Secret": "secret-123"}, update)
    second = await receiver.receive({"X-Max-Bot-Api-Secret": "secret-123"}, update)

    assert first.status_code == 200
    assert first.accepted is True
    assert first.duplicate is False
    assert second.status_code == 200
    assert second.accepted is True
    assert second.duplicate is True
    assert await receiver.queue.get() == update


@pytest.mark.asyncio
async def test_receiver_rejects_wrong_secret_without_queueing() -> None:
    receiver = MaxWebhookReceiver("secret-123")

    result = await receiver.receive({"X-Max-Bot-Api-Secret": "wrong"}, {"update_type": "bot_started"})

    assert result.status_code == 403
    assert result.accepted is False
    assert receiver.queue.empty()


@pytest.mark.asyncio
async def test_receiver_acknowledges_durable_event_when_memory_queue_is_full() -> None:
    receiver = MaxWebhookReceiver("secret-123", max_queue_size=1)
    first = {"update_type": "message_created", "message": {"body": {"mid": "mid-1"}}}
    second = {"update_type": "message_created", "message": {"body": {"mid": "mid-2"}}}

    first_result = await receiver.receive({"X-Max-Bot-Api-Secret": "secret-123"}, first)
    second_result = await receiver.receive({"X-Max-Bot-Api-Secret": "secret-123"}, second)

    assert first_result.status_code == 200
    assert second_result.status_code == 200
    assert second_result.accepted is True


@pytest.mark.asyncio
async def test_receiver_reuses_pending_event_after_receiver_restart(tmp_path) -> None:
    inbox = tmp_path / "max-inbox.sqlite3"
    update = {"update_type": "message_created", "message": {"body": {"mid": "mid-1"}}}

    first = MaxWebhookReceiver("secret-123", inbox_path=inbox)
    initial = await first.receive({"X-Max-Bot-Api-Secret": "secret-123"}, update)
    assert initial.accepted is True

    restarted = MaxWebhookReceiver("secret-123", inbox_path=inbox)
    duplicate = await restarted.receive({"X-Max-Bot-Api-Secret": "secret-123"}, update)

    assert duplicate.status_code == 200
    assert duplicate.duplicate is True
    assert await restarted.next_pending() == update
    await restarted.mark_processed(update)
    assert await restarted.next_pending() is None
