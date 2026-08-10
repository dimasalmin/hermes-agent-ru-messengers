from __future__ import annotations

from plugins.vk.state import VkStateStore


def test_vk_state_store_deduplicates_messages_and_persists_long_poll_marker(tmp_path):
    store = VkStateStore(str(tmp_path / "vk.sqlite3"))
    try:
        assert store.claim_message("42") is True
        assert store.claim_message("42") is False
        store.set_long_poll_state("server", "key", "17")
    finally:
        store.close()

    reopened = VkStateStore(str(tmp_path / "vk.sqlite3"))
    try:
        assert reopened.get_long_poll_state() == ("server", "key", "17")
        assert reopened.claim_message("42") is False
    finally:
        reopened.close()


def test_vk_state_store_uses_schema_version_and_single_poll_lease(tmp_path):
    path = str(tmp_path / "vk.sqlite3")
    first = VkStateStore(path)
    second = VkStateStore(path)
    try:
        assert first.schema_version() == 1
        assert first.acquire_poll_lock("owner-a", ttl_seconds=60, now=100.0) is True
        assert second.acquire_poll_lock("owner-b", ttl_seconds=60, now=120.0) is False
        assert first.refresh_poll_lock("owner-a", ttl_seconds=60, now=130.0) is True
        assert second.acquire_poll_lock("owner-b", ttl_seconds=60, now=191.0) is True
        assert first.release_poll_lock("owner-a") is False
        assert second.release_poll_lock("owner-b") is True
    finally:
        first.close()
        second.close()


def test_vk_state_store_hashes_pairing_codes_and_expires_them(tmp_path):
    store = VkStateStore(str(tmp_path / "vk.sqlite3"))
    try:
        code = store.issue_pairing_code("100000001", ttl_seconds=60, now=100.0)
        assert len(code) == 6 and code.isdigit()
        assert store.is_paired("100000001") is False
        assert store.approve_pairing("100000001", "000000", now=101.0) is False
        assert store.approve_pairing("100000001", code, now=101.0) is True
        assert store.is_paired("100000001") is True
        assert store.approve_pairing("100000001", code, now=102.0) is False

        expired = store.issue_pairing_code("3237809", ttl_seconds=1, now=200.0)
        assert store.approve_pairing("3237809", expired, now=202.0) is False
    finally:
        store.close()
