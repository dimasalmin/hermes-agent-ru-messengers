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


@pytest.mark.asyncio
async def test_distinct_callbacks_on_one_message_are_not_deduplicated() -> None:
    receiver = MaxWebhookReceiver("secret-123")
    headers = {"X-Max-Bot-Api-Secret": "secret-123"}
    callbacks = [
        {
            "update_type": "message_callback",
            "callback": {
                "callback_id": callback_id,
                "message": {"body": {"mid": "same-message"}},
            },
        }
        for callback_id in ("callback-a", "callback-b")
    ]

    results = [await receiver.receive(headers, update) for update in callbacks]

    assert all(result.accepted for result in results)
    assert all(not result.duplicate for result in results)
    assert await receiver.pending_updates(limit=10) == callbacks


@pytest.mark.asyncio
async def test_webhook_claims_control_updates_before_media_updates() -> None:
    receiver = MaxWebhookReceiver("secret-123")
    headers = {"X-Max-Bot-Api-Secret": "secret-123"}
    media = {
        "update_type": "message_created",
        "message": {"body": {"mid": "media", "attachments": [{"type": "file"}]}},
    }
    callback = {"update_type": "message_callback", "callback": {"callback_id": "cb"}}
    await receiver.receive(headers, media)
    await receiver.receive(headers, callback)

    assert await receiver.claim_next(control_only=True) == callback
    assert await receiver.claim_next(control_only=False) == media


@pytest.mark.asyncio
async def test_webhook_restart_marks_inflight_work_unknown_without_replay(tmp_path) -> None:
    inbox = tmp_path / "max-inbox.sqlite3"
    update = {"update_type": "message_created", "update_id": 51}
    receiver = MaxWebhookReceiver("secret-123", inbox_path=inbox)
    await receiver.receive({"X-Max-Bot-Api-Secret": "secret-123"}, update)
    assert await receiver.claim_next() == update
    await receiver.mark_accepted(update)
    await receiver.close()

    restarted = MaxWebhookReceiver("secret-123", inbox_path=inbox)
    assert await restarted.recover_inflight() == 1
    assert restarted.status_summary()["unknown"] == 1
    assert await restarted.claim_next() is None
    duplicate = await restarted.receive({"X-Max-Bot-Api-Secret": "secret-123"}, update)
    assert duplicate.duplicate is True
    assert duplicate.accepted is False
    await restarted.close()


@pytest.mark.asyncio
async def test_webhook_event_key_migration_preserves_processed_callback(tmp_path) -> None:
    import sqlite3

    inbox = tmp_path / "max-inbox.sqlite3"
    update = {
        "update_type": "message_callback",
        "callback": {
            "callback_id": "callback-old",
            "message": {"body": {"mid": "same-message"}},
        },
    }
    headers = {"X-Max-Bot-Api-Secret": "secret-123"}
    receiver = MaxWebhookReceiver("secret-123", inbox_path=inbox)
    await receiver.receive(headers, update)
    await receiver.mark_processed(update)
    await receiver.close()

    conn = sqlite3.connect(inbox)
    conn.execute(
        "UPDATE max_webhook_inbox SET event_key = 'message:same-message'"
    )
    conn.commit()
    conn.close()

    migrated = MaxWebhookReceiver("secret-123", inbox_path=inbox)
    duplicate = await migrated.receive(headers, update)
    assert duplicate.duplicate is True
    assert duplicate.accepted is False
    assert migrated.status_summary()["processed"] == 1
    await migrated.close()
