"""Operator CLI for VK state and pairing administration."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any

from .state import VkStateStore


def _default_state_path() -> str:
    home = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
    return os.environ.get("VK_STATE_PATH", str(home / "vk" / "state.sqlite3"))


def setup_cli(parser: Any) -> None:
    parser.add_argument("--state-path", default=None, help="SQLite state path")
    actions = parser.add_subparsers(dest="vk_action", required=True)
    status = actions.add_parser("status", help="Show redacted VK state status")
    status.add_argument("--state-path", default=argparse.SUPPRESS, help="SQLite state path")
    validate = actions.add_parser("validate", help="Validate VK configuration")
    validate.add_argument("--live", action="store_true", help="Call VK groups.getLongPollServer")
    validate.add_argument("--state-path", default=argparse.SUPPRESS, help="SQLite state path")
    pairing = actions.add_parser("pairing", help="Manage DM pairing")
    pairing.add_argument("--state-path", default=argparse.SUPPRESS, help="SQLite state path")
    pairing_actions = pairing.add_subparsers(dest="pairing_action", required=True)
    issue = pairing_actions.add_parser("issue", help="Issue a short-lived pairing code")
    issue.add_argument("--user-id", required=True)
    issue.add_argument("--ttl", type=float, default=600.0)
    issue.add_argument("--state-path", default=argparse.SUPPRESS, help="SQLite state path")
    revoke = pairing_actions.add_parser("revoke", help="Revoke pairing for a user")
    revoke.add_argument("--user-id", required=True)
    revoke.add_argument("--state-path", default=argparse.SUPPRESS, help="SQLite state path")


def handle_cli(args: argparse.Namespace) -> int:
    if args.vk_action == "validate":
        from .adapter import _local_env_values

        dotenv = _local_env_values()
        token = (os.environ.get("VK_GROUP_TOKEN") or dotenv.get("VK_GROUP_TOKEN", "")).strip()
        group_id = (os.environ.get("VK_GROUP_ID") or dotenv.get("VK_GROUP_ID", "")).strip()
        configured = bool(token and group_id.isdigit() and int(group_id) > 0)
        print(f"configured={'true' if configured else 'false'}")
        if not configured:
            return 2
        if args.live:
            from .client import VkApiClient

            async def probe() -> None:
                client = VkApiClient(token, int(group_id))
                try:
                    await client.get_long_poll_server()
                finally:
                    await client.close()

            asyncio.run(probe())
            print("long_poll=ok")
        return 0

    path = str(args.state_path or _default_state_path())
    store = VkStateStore(path)
    try:
        if args.vk_action == "status":
            print(f"schema_version={store.schema_version()}")
            print(f"long_poll_state={'present' if store.get_long_poll_state() else 'absent'}")
            return 0
        if args.vk_action == "pairing" and args.pairing_action == "issue":
            print(store.issue_pairing_code(str(args.user_id), ttl_seconds=args.ttl))
            return 0
        if args.vk_action == "pairing" and args.pairing_action == "revoke":
            revoked = store.revoke_pairing(str(args.user_id))
            print("revoked" if revoked else "not-found")
            return 0
        print("unknown VK action")
        return 2
    finally:
        store.close()
