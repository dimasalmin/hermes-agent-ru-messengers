"""Read-only live VK Community Long Poll smoke using local Hermes .env."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from vk_live_validate import _read_env

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from plugins.vk.client import VkApiClient  # noqa: E402


async def main_async(env_file: Path, wait: int) -> int:
    values = _read_env(env_file.expanduser())
    token = values.get("VK_GROUP_TOKEN", "")
    group_id = int(values.get("VK_GROUP_ID", "0"))
    client = VkApiClient(token, group_id)
    try:
        server = await client.get_long_poll_server()
        result = await client.long_poll_check(
            str(server["server"]),
            str(server["key"]),
            str(server.get("ts") or "0"),
            wait=wait,
        )
        print(
            {
                "connected": True,
                "failed": result.failed,
                "needs_reinit": result.needs_reinit,
                "updates_received": len(result.updates),
                "next_ts_present": bool(result.ts),
            }
        )
        return 0
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path("~/.hermes/.env").expanduser())
    parser.add_argument("--wait", type=int, default=5)
    args = parser.parse_args()
    return asyncio.run(main_async(args.env_file, max(1, min(args.wait, 30))))


if __name__ == "__main__":
    raise SystemExit(main())
