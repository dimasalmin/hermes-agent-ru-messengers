"""Self-contained helpers required when only the VK plugin is installed."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Set


def split_message(text: str, max_len: int) -> list[str]:
    if max_len <= 0:
        raise ValueError("max_len must be positive")
    if not text:
        return []
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_len:
        window = remaining[:max_len]
        cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(". "), window.rfind("! "), window.rfind("? "), window.rfind(" "))
        if cut <= max_len // 2:
            cut = max_len
        elif window[cut:cut + 2] in {". ", "! ", "? "}:
            cut += 2
        else:
            cut += 1
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


MEDIA_TAG_RE = re.compile(r"MEDIA:(\S+)")


def extract_media_tags(content: str) -> tuple[list[str], str]:
    paths = MEDIA_TAG_RE.findall(content)
    if not paths:
        return [], content
    cleaned = MEDIA_TAG_RE.sub("", content)
    return paths, re.sub(r"\n{3,}", "\n\n", cleaned).strip()


ALWAYS_ALLOWED_COMMANDS = frozenset({"help", "whoami"})


def _parse_ids(value: Optional[str]) -> Set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


@dataclass
class AccessPolicy:
    allowed_users: Set[str] = field(default_factory=set)
    group_allowed_users: Set[str] = field(default_factory=set)
    group_allowed_chats: Set[str] = field(default_factory=set)
    admin_users: Set[str] = field(default_factory=set)
    user_allowed_commands: Set[str] = field(default_factory=set)
    group_user_allowed_commands: Set[str] = field(default_factory=set)
    guest_mode: bool = False
    allow_all: bool = False

    @classmethod
    def from_env_and_extra(cls, *, allowed_users_env: Optional[str], group_allowed_users_env: Optional[str], group_allowed_chats_env: Optional[str], allow_all_env: Optional[str], guest_mode_env: Optional[str], extra: Optional[dict] = None) -> "AccessPolicy":
        extra = extra or {}

        def ids(key: str) -> Set[str]:
            value = extra.get(key)
            if isinstance(value, str):
                return _parse_ids(value)
            if isinstance(value, (list, tuple, set)):
                return {str(item).strip() for item in value if str(item).strip()}
            return set()

        return cls(
            allowed_users=_parse_ids(allowed_users_env) | ids("allow_from"),
            group_allowed_users=_parse_ids(group_allowed_users_env) | ids("group_allow_from"),
            group_allowed_chats=_parse_ids(group_allowed_chats_env) | ids("group_allowed_chats"),
            admin_users=ids("allow_admin_from"),
            user_allowed_commands=ids("user_allowed_commands"),
            group_user_allowed_commands=ids("group_user_allowed_commands"),
            guest_mode=str(guest_mode_env or "").lower() in {"1", "true", "yes"},
            allow_all=str(allow_all_env or "").lower() in {"1", "true", "yes"},
        )

    def can_dm(self, user_id: str) -> bool:
        return self.allow_all or user_id in self.allowed_users

    def can_group(self, user_id: str, chat_id: str, *, mentioned: bool) -> bool:
        return self.allow_all or user_id in self.allowed_users or user_id in self.group_allowed_users or chat_id in self.group_allowed_chats or (self.guest_mode and mentioned)

    def can_run_command(self, user_id: str, command: str, *, is_group: bool) -> bool:
        cmd = command.lstrip("/").split("@", 1)[0].split(" ", 1)[0].lower()
        if cmd in ALWAYS_ALLOWED_COMMANDS or user_id in self.admin_users:
            return True
        if not self.admin_users and not self.user_allowed_commands and not self.group_user_allowed_commands:
            return True
        allowed = self.group_user_allowed_commands if is_group else self.user_allowed_commands
        return cmd in allowed
