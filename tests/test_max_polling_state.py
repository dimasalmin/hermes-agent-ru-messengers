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
