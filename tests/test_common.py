"""Tests for shared utilities — these run without Hermes core installed."""

from __future__ import annotations

import pytest

from plugins._ru_common import AccessPolicy, extract_media_tags, split_message
from plugins._ru_common.access import parse_id_list


# ---------------------------------------------------------------------------
# AccessPolicy
# ---------------------------------------------------------------------------


def test_parse_id_list_handles_whitespace_and_blanks():
    assert parse_id_list(" 1, 2 ,  3,, ") == {"1", "2", "3"}
    assert parse_id_list(None) == set()
    assert parse_id_list("") == set()


def test_access_dm_requires_explicit_allowlist():
    p = AccessPolicy(allowed_users={"42"})
    assert p.can_dm("42") is True
    assert p.can_dm("99") is False


def test_access_allow_all_overrides_lists():
    p = AccessPolicy(allow_all=True)
    assert p.can_dm("anyone") is True
    assert p.can_group("anyone", "chat", mentioned=False) is True


def test_access_group_allows_via_chat_allowlist():
    p = AccessPolicy(group_allowed_chats={"-100123"})
    assert p.can_group("99", "-100123", mentioned=False) is True
    assert p.can_group("99", "-100999", mentioned=False) is False


def test_access_guest_mode_requires_mention():
    p = AccessPolicy(guest_mode=True)
    assert p.can_group("99", "any", mentioned=True) is True
    assert p.can_group("99", "any", mentioned=False) is False


def test_access_commands_default_unrestricted_without_admin_config():
    p = AccessPolicy()
    assert p.can_run_command("user", "/anything", is_group=False) is True


def test_access_commands_restricted_when_admin_list_present():
    p = AccessPolicy(admin_users={"1"}, user_allowed_commands={"status"})
    # Admin can run anything.
    assert p.can_run_command("1", "/dangerous", is_group=False) is True
    # Non-admin restricted to allowed list...
    assert p.can_run_command("2", "/status", is_group=False) is True
    assert p.can_run_command("2", "/dangerous", is_group=False) is False
    # ...except the always-allowed commands.
    assert p.can_run_command("2", "/help", is_group=False) is True
    assert p.can_run_command("2", "/whoami", is_group=False) is True


def test_access_command_strips_bot_suffix_and_args():
    p = AccessPolicy(admin_users={"1"}, user_allowed_commands={"model"})
    assert p.can_run_command("2", "/model@mybot please", is_group=False) is True


def test_access_from_env_and_extra_merges_sources():
    p = AccessPolicy.from_env_and_extra(
        allowed_users_env="1,2",
        group_allowed_users_env=None,
        group_allowed_chats_env=None,
        allow_all_env=None,
        guest_mode_env="true",
        extra={"allow_from": ["3"], "allow_admin_from": ["1"]},
    )
    assert p.allowed_users == {"1", "2", "3"}
    assert p.admin_users == {"1"}
    assert p.guest_mode is True


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def test_split_short_message_returns_single_chunk():
    assert split_message("hello", 100) == ["hello"]


def test_split_empty_message_returns_empty_list():
    assert split_message("", 100) == []


def test_split_prefers_paragraph_boundary():
    text = "a" * 30 + "\n\n" + "b" * 30
    chunks = split_message(text, 35)
    assert len(chunks) == 2
    assert chunks[0].rstrip() == "a" * 30
    assert chunks[1] == "b" * 30


def test_split_respects_max_length():
    text = "x" * 1000
    chunks = split_message(text, 100)
    assert all(len(c) <= 110 for c in chunks)  # +small overhead for fence reopen
    assert "".join(chunks).replace(" ", "").startswith("x" * 100)


def test_split_max_len_zero_raises():
    with pytest.raises(ValueError):
        split_message("x", 0)


# ---------------------------------------------------------------------------
# Media tags
# ---------------------------------------------------------------------------


def test_extract_media_tags_strips_and_returns_paths():
    paths, cleaned = extract_media_tags("here is a file MEDIA:/tmp/a.png done")
    assert paths == ["/tmp/a.png"]
    assert "MEDIA:" not in cleaned
    assert "here is a file" in cleaned and "done" in cleaned


def test_extract_media_tags_preserves_order_with_multiple():
    paths, _ = extract_media_tags("MEDIA:/a.png MEDIA:/b.pdf text MEDIA:/c.ogg")
    assert paths == ["/a.png", "/b.pdf", "/c.ogg"]


def test_extract_media_tags_returns_empty_when_none():
    paths, cleaned = extract_media_tags("plain text")
    assert paths == []
    assert cleaned == "plain text"
