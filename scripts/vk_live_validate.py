"""Read-only validation of local VK credentials.

The script reads ``~/.hermes/.env`` inside the runtime environment and never
prints the token. It does not send a message or modify Hermes state.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import httpx


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path("~/.hermes/.env").expanduser())
    args = parser.parse_args()
    env_path = args.env_file.expanduser()
    if not env_path.is_file():
        print({"env_exists": False, "path": str(env_path)})
        return 2
    values = _read_env(env_path)
    token = values.get("VK_GROUP_TOKEN", "")
    group_id = values.get("VK_GROUP_ID", "")
    print(
        {
            "env_exists": True,
            "token_present": bool(token),
            "token_length": len(token),
            "prefix": token[:6],
            "suffix": token[-3:],
            "group_id": group_id,
            "allowed_users_present": bool(values.get("VK_ALLOWED_USERS")),
        }
    )
    if not token or not group_id:
        return 2
    response = httpx.post(
        "https://api.vk.com/method/groups.getLongPollServer",
        data={"group_id": group_id, "access_token": token, "v": "5.199"},
        timeout=30,
    )
    payload = response.json()
    if "error" in payload:
        error = payload["error"]
        print(
            {
                "http_status": response.status_code,
                "ok": False,
                "error_code": error.get("error_code"),
                "error_msg": error.get("error_msg"),
            }
        )
        return 1
    result = payload.get("response", {})
    print(
        {
            "http_status": response.status_code,
            "ok": bool(result.get("server") and result.get("key") and result.get("ts")),
            "has_server": bool(result.get("server")),
            "has_key": bool(result.get("key")),
            "has_ts": bool(result.get("ts")),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
