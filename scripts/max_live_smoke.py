"""Live MAX API smoke without writing tokens or message text to disk/logs.

Required environment:
    MAX_BOT_TOKEN
    MAX_CA_BUNDLE (when the host trust store lacks the Russian MAX chain)

Example:
    MAX_BOT_TOKEN=... MAX_CA_BUNDLE=/tmp/max-ca-bundle.pem \
      python scripts/max_live_smoke.py --user-id 100000001 --send --poll-seconds 30
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any, Mapping

from plugins.max.client import DEFAULT_API_BASE, MaxClient
from plugins.max.tls import tls_verify_from_env


def _message_id(response: Mapping[str, Any]) -> str:
    message = response.get("message")
    if isinstance(message, Mapping):
        body = message.get("body")
        if isinstance(body, Mapping) and body.get("mid"):
            return str(body["mid"])
    for key in ("message_id", "id"):
        if response.get(key):
            return str(response[key])
    return ""


def _update_summary(update: Mapping[str, Any]) -> str:
    message = update.get("message")
    body = message.get("body") if isinstance(message, Mapping) else None
    sender = message.get("sender") if isinstance(message, Mapping) else None
    mid = body.get("mid") if isinstance(body, Mapping) else ""
    user_id = sender.get("user_id") if isinstance(sender, Mapping) else ""
    return (
        f"update_type={update.get('update_type')} "
        f"update_id={update.get('update_id')} message_mid={mid} user_id={user_id}"
    )


async def _run(args: argparse.Namespace) -> int:
    token = os.environ.get("MAX_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("MAX_BOT_TOKEN is required")
    verify = tls_verify_from_env()
    client = MaxClient(
        token,
        base_url=os.environ.get("MAX_API_BASE_URL", DEFAULT_API_BASE),
        verify=verify,
    )
    try:
        bot = await client.get_me()
        print(f"me_status=ok user_id={bot.get('user_id') or bot.get('id')} username={bot.get('username')}")

        if args.send:
            if not args.user_id:
                raise SystemExit("--user-id is required with --send")
            response = await client.send_message(
                str(args.user_id),
                args.message,
                target_type="user",
            )
            print(f"send_status=ok message_mid={_message_id(response)} target_user_id={args.user_id}")

        if args.poll_seconds:
            result = await client.get_updates(
                timeout=min(int(args.poll_seconds), 90),
                limit=100,
            )
            updates = result.get("updates", []) or []
            print(f"poll_status=ok updates={len(updates)} marker={result.get('marker')}")
            for update in updates:
                if isinstance(update, Mapping):
                    print(_update_summary(update))
    finally:
        await client.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", default=os.environ.get("MAX_TEST_USER_ID", ""))
    parser.add_argument("--send", action="store_true")
    parser.add_argument(
        "--message",
        default="Hermes/MAX transport smoke: РѕС‚РІРµС‚СЊС‚Рµ РєРѕСЂРѕС‚РєРёРј С‚РµРєСЃС‚РѕРј РґР»СЏ РїСЂРѕРІРµСЂРєРё inbound.",
    )
    parser.add_argument("--poll-seconds", type=int, default=0)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
