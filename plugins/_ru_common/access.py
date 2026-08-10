"""Allowlist / admin-tier access policy shared by MAX and VK adapters.

Mirrors Telegram adapter's authorization model:
  - users allowed in DM and groups
  - users allowed only in groups (no DM access)
  - chats where any member can invoke the bot
  - guest mode: non-allowlisted groups respond on explicit @mention
  - admin tier with restricted slash-command surface
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Set


ALWAYS_ALLOWED_COMMANDS = frozenset({"help", "whoami"})


def parse_id_list(value: Optional[str]) -> Set[str]:
    if not value:
        return set()
    return {part.strip() for part in value.split(",") if part.strip()}


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
    def from_env_and_extra(
        cls,
        *,
        allowed_users_env: Optional[str],
        group_allowed_users_env: Optional[str],
        group_allowed_chats_env: Optional[str],
        allow_all_env: Optional[str],
        guest_mode_env: Optional[str],
        extra: Optional[dict] = None,
    ) -> "AccessPolicy":
        extra = extra or {}

        def _from_extra(key: str) -> Set[str]:
            value = extra.get(key)
            if not value:
                return set()
            if isinstance(value, str):
                return parse_id_list(value)
            return {str(x).strip() for x in value if str(x).strip()}

        return cls(
            allowed_users=parse_id_list(allowed_users_env) | _from_extra("allow_from"),
            group_allowed_users=parse_id_list(group_allowed_users_env) | _from_extra("group_allow_from"),
            group_allowed_chats=parse_id_list(group_allowed_chats_env) | _from_extra("group_allowed_chats"),
            admin_users=_from_extra("allow_admin_from"),
            user_allowed_commands=_from_extra("user_allowed_commands"),
            group_user_allowed_commands=_from_extra("group_user_allowed_commands"),
            guest_mode=(guest_mode_env or "").lower() in {"1", "true", "yes"},
            allow_all=(allow_all_env or "").lower() in {"1", "true", "yes"},
        )

    def can_dm(self, user_id: str) -> bool:
        if self.allow_all:
            return True
        return user_id in self.allowed_users

    def can_group(self, user_id: str, chat_id: str, *, mentioned: bool) -> bool:
        if self.allow_all:
            return True
        if user_id in self.allowed_users or user_id in self.group_allowed_users:
            return True
        if chat_id in self.group_allowed_chats:
            return True
        if self.guest_mode and mentioned:
            return True
        return False

    def can_run_command(
        self,
        user_id: str,
        command: str,
        *,
        is_group: bool,
    ) -> bool:
        cmd = command.lstrip("/").split("@", 1)[0].split(" ", 1)[0].lower()
        if cmd in ALWAYS_ALLOWED_COMMANDS:
            return True
        if user_id in self.admin_users:
            return True
        # Backward-compat: if no admin list was configured, treat as unrestricted
        if not self.admin_users and not self.user_allowed_commands and not self.group_user_allowed_commands:
            return True
        allowed = self.group_user_allowed_commands if is_group else self.user_allowed_commands
        return cmd in allowed
