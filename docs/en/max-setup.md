# MAX setup for Hermes Agent

This document describes the current MVP. It does not promise that MAX is
available during every regional mobile-network restriction.

## 1. Create a MAX bot

Use the current MAX for Business workflow and obtain a Bot API token. The old
`@MasterBot`/`/newbot` instructions are intentionally not used here because
MAX's account and verification rules have changed.

Official references:

- <https://dev.max.ru/docs/chatbots/bots-coding/prepare>
- <https://dev.max.ru/docs-api>

## 2. Install without touching Hermes core

```powershell
python -m pip install -e ".[dev]"
New-Item -ItemType Junction `
  -Path "$HOME\.hermes\plugins\max" `
  -Target "<path-to-repository>\plugins\max"
```

The installed directory must contain `plugin.yaml`, not `PLUGIN.yaml`.

## 3. Configuration

```env
MAX_BOT_TOKEN=...
MAX_ALLOWED_USERS=123456789
# Optional: per-attachment limit; default is 50 MiB.
MAX_MEDIA_MAX_BYTES=52428800
```

With no Webhook URL, the adapter uses Long Polling for development and smoke
tests. MAX documents polling as a development/test transport, not a production
transport.

## 4. TLS

The API base is `https://platform-api2.max.ru`. Keep TLS verification enabled.
If the host trust store does not contain the required chain, create a verified
PEM bundle and set:

```env
MAX_CA_BUNDLE=/etc/hermes/max-ca-bundle.pem
```

Do not use `verify=False` or an insecure curl check as an operational fix.

## 5. Production Webhook prerequisites

```env
MAX_WEBHOOK_URL=https://example.ru/hermes/max
MAX_WEBHOOK_SECRET=<5-256 characters: letters, digits, underscore or hyphen>
```

MAX requires HTTPS on port 443, a trusted certificate and HTTP 200 within 30
seconds. The plugin provides `MaxAdapter.handle_webhook()` and a bounded queue;
an HTTP/ASGI ingress or reverse-proxy integration must call this method. The
current text MVP does not start a public listener inside the Hermes gateway. An
optional separate ingress process is included:

```powershell
python -m pip install -e ".[webhook]"
python scripts/max_webhook_server.py
```

Keep `MAX_INBOX_PATH` identical in the ingress process and the Hermes plugin.
TLS termination and public port 443 remain reverse-proxy responsibilities.

## 6. Security policy

The default is deny. Put only trusted numeric MAX user IDs in
`MAX_ALLOWED_USERS`. Group access additionally uses
`MAX_GROUP_ALLOWED_USERS` and `MAX_GROUP_ALLOWED_CHATS`. Do not enable
`MAX_ALLOW_ALL_USERS` on a public bot.

The Hermes global authorization registry runs before the adapter. Therefore
users who are allowed only in groups still need to be present in
`MAX_ALLOWED_USERS`; the group variables narrow the adapter decision and do not
bypass Hermes global authorization.

## 7. Current status and limitations

- Text and text chunking are implemented.
- Inbound media is cached locally through Hermes; outbound `MEDIA:` files use
  the current MAX `/uploads` and `payload.token` contract.
- Media has contract tests but still needs disposable-bot acceptance.
- Callback approval buttons are wired to Hermes resolvers.
- Streaming edits are present as an adapter API but require rate-limit and
  message-age integration tests before production use.
- Store availability and whitelist behavior require a dated operator/region
  field test.

## 8. Verification and rollback

```powershell
python -m pytest -q
```

For a live transport check, provide `MAX_BOT_TOKEN` and `MAX_CA_BUNDLE` only in
the shell environment, then run `scripts/max_live_smoke.py`. The adapter-level
check `scripts/max_adapter_live_smoke.py` uses temporary SQLite state and a
collector instead of Hermes model execution.

Before restarting Hermes, run the loader import smoke against the current
Hermes installation. To roll back, stop the gateway, remove or retarget only
`~/.hermes/plugins/max`, then start Hermes again. Hermes core files are not
changed by this plugin.
