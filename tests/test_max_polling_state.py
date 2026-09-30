from __future__ import annotations

from plugins.max.polling_state import MaxTargetStore, PollingMarkerStore


def test_marker_survives_store_reopen(tmp_path) -> None:
    path = tmp_path / "polling.sqlite3"
    first = PollingMarkerStore(path)
    assert first.get() is None
    first.set(123)
    first.close()

    second = PollingMarkerStore(path)
    assert second.get() == 123
    second.close()


def test_target_type_survives_store_reopen(tmp_path) -> None:
    path = tmp_path / "targets.sqlite3"
    first = MaxTargetStore(path)
    assert first.get("chat-1") is None
    first.set("chat-1", "chat")
    first.close()

    second = MaxTargetStore(path)
    assert second.get("chat-1") == "chat"
    second.close()


def test_polling_batch_commits_updates_and_marker_together(tmp_path) -> None:
    path = tmp_path / "polling.sqlite3"
    store = PollingMarkerStore(path)
    update = {"update_type": "message_created", "update_id": 7}

    store.accept_batch([update], 99)

    assert store.get() == 99
    pending = store.claim_next()
    assert pending == update
    store.mark_processed(update)
    summary = store.status_summary()
    assert summary["processed"] == 1
    assert summary["pending"] == 0
    store.close()


def test_polling_failed_update_is_visible_and_not_replayed(tmp_path) -> None:
    path = tmp_path / "polling.sqlite3"
    store = PollingMarkerStore(path)
    update = {"update_type": "message_created", "update_id": 8}

    store.accept_batch([update], 100)
    assert store.claim_next() == update
    store.mark_failed(update, "MAX API 429")

    assert store.claim_next() is None
    summary = store.status_summary()
    assert summary["failed"] == 1
    assert summary["last_error"] == "MAX API 429"
    store.close()


def test_distinct_callbacks_on_one_message_are_not_deduplicated(tmp_path) -> None:
    store = PollingMarkerStore(tmp_path / "polling.sqlite3")
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

    store.accept_batch(callbacks, 11)

    claimed = [store.claim_next(), store.claim_next()]
    assert {item["callback"]["callback_id"] for item in claimed if item} == {
        "callback-a",
        "callback-b",
    }
    assert store.status_summary()["dispatching"] == 2
    store.close()


def test_event_key_migration_preserves_processed_callbacks(tmp_path) -> None:
    path = tmp_path / "polling.sqlite3"
    update = {
        "update_type": "message_callback",
        "callback": {
            "callback_id": "callback-old",
            "message": {"body": {"mid": "same-message"}},
        },
    }
    store = PollingMarkerStore(path)
    store.accept_batch([update], 12)
    assert store.claim_next() == update
    store.mark_processed(update)
    store.close()

    import sqlite3

    conn = sqlite3.connect(path)
    conn.execute(
        "UPDATE max_polling_inbox SET event_key = 'message:same-message'"
    )
    conn.commit()
    conn.close()

    migrated = PollingMarkerStore(path)
    migrated.accept_batch([update], 13)
    summary = migrated.status_summary()
    assert summary["processed"] == 1
    assert summary["pending"] == 0
    assert migrated.get() == 13
    migrated.close()


def test_event_key_migration_does_not_requeue_inflight_callback(tmp_path) -> None:
    import json
    import sqlite3

    path = tmp_path / "polling.sqlite3"
    store = PollingMarkerStore(path)
    store.close()

    update = {
        "update_type": "message_callback",
        "callback": {
            "callback_id": "callback-inflight",
            "message": {"body": {"mid": "same-message"}},
        },
    }
    conn = sqlite3.connect(path)
    payload = json.dumps(update, ensure_ascii=False, separators=(",", ":"))
    conn.executemany(
        "INSERT INTO max_polling_inbox "
        "(event_key, payload, status, attempts, created_at) VALUES (?, ?, ?, ?, ?)",
        [
            ("callback:callback-inflight", payload, "pending", 0, 1.0),
            ("message:same-message", payload, "accepted", 1, 2.0),
        ],
    )
    conn.commit()
    conn.close()

    migrated = PollingMarkerStore(path)
    assert migrated.status_summary()["accepted"] == 1
    assert migrated.claim_next() is None
    migrated.close()


def test_control_updates_have_a_reserved_claim_lane(tmp_path) -> None:
    store = PollingMarkerStore(tmp_path / "polling.sqlite3")
    media = {
        "update_type": "message_created",
        "message": {"body": {"mid": "media", "attachments": [{"type": "file"}]}},
    }
    callback = {"update_type": "message_callback", "callback": {"callback_id": "cb"}}
    store.accept_batch([media, callback], 14)

    assert store.claim_next(control_only=False) == media
    assert store.claim_next(control_only=True) == callback
    store.close()


def test_dispatch_state_transitions_are_monotonic(tmp_path) -> None:
    store = PollingMarkerStore(tmp_path / "polling.sqlite3")
    update = {"update_type": "message_created", "update_id": 15}
    store.accept_batch([update], 15)
    store.claim_next()

    store.mark_accepted(update)
    assert store.status_summary()["accepted"] == 1
    store.mark_processing_key("update:15")
    assert store.status_summary()["processing"] == 1
    store.mark_accepted(update)
    assert store.status_summary()["processing"] == 1
    store.mark_processed_key("update:15")
    assert store.status_summary()["processed"] == 1
    store.close()


def test_restart_marks_inflight_work_unknown_without_replay(tmp_path) -> None:
    path = tmp_path / "polling.sqlite3"
    update = {"update_type": "message_created", "update_id": 16}
    first = PollingMarkerStore(path)
    first.accept_batch([update], 16)
    first.claim_next()
    first.mark_accepted(update)
    first.close()

    restarted = PollingMarkerStore(path)
    assert restarted.recover_inflight() == 1
    assert restarted.status_summary()["unknown"] == 1
    assert restarted.claim_next() is None
    assert "повтор" in restarted.status_summary()["last_error"].lower()
    restarted.close()
