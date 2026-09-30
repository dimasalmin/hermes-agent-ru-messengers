# MAX setup for Hermes Agent

This document describes the current external plugin. It does not promise that MAX is
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
  -Target "D:\Cloude\Ai Agents\hermes-agent-ru-messengers\plugins\max"
```

The installed directory must contain `plugin.yaml`, not `PLUGIN.yaml`.

## 3. Configuration

```env
MAX_BOT_TOKEN=...
MAX_ALLOWED_USERS=123456789
# Groups require both lists.
MAX_GROUP_ALLOWED_USERS=123456789
MAX_GROUP_ALLOWED_CHATS=123456789
MAX_ADMIN_USERS=123456789
# Optional: per-attachment limit; default and hard cap are 50,000,000 bytes.
MAX_MEDIA_MAX_BYTES=50000000
```

With no Webhook URL, the adapter uses Long Polling for development and smoke
tests. MAX documents polling as a development/test transport, not a production
transport.

## 4. TLS

The API base is `https://platform-api2.max.ru`. Keep TLS verification enabled.
The plugin keeps the host trust store and adds certificates from the configured
PEM bundle. If the host trust store does not contain the required MAX chain, set:

```env
MAX_CA_BUNDLE=/etc/hermes/max-ca-bundle.pem
```

The bundle may contain only the current MAX chain. Do not use `verify=False` or
an insecure curl check as an operational fix.

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
`MAX_ALLOWED_USERS`. Group access is an AND check: the sender must be allowed
and the chat must be listed in `MAX_GROUP_ALLOWED_CHATS`. `MAX_ADMIN_USERS`
restricts approvals and control actions in groups. Do not enable
`MAX_ALLOW_ALL_USERS` on a public bot.

The Hermes global authorization registry runs before the adapter. Therefore
users who are allowed only in groups still need to be present in
`MAX_ALLOWED_USERS`; the group variables narrow the adapter decision and do not
bypass Hermes global authorization.

## 7. Current status and limitations

- Text and text chunking are implemented.
- Image, document, audio/voice and video delivery is wired to Hermes' native
  adapter methods in both directions; inbound bytes use Hermes' existing
  cache, and outbound files use `/uploads` plus multipart `data`.
- The command menu, `/menu`, `/start`, `/commands`, `/maxstatus`, native buttons
  and group participant-scoped sessions are implemented. `/start`, `/menu` and
  `/commands` include a text fallback when a MAX client hides the native menu.
- Outbound image and document upload/delivery have been live-smoked; inbound
  media, phone rendering and model-use acceptance remain pending.
- Media batching follows MAX's restriction: image/video batches are limited to
  12 items, while files and audio are sent in compatible separate messages.
- Streaming edits are present as an adapter API but require rate-limit and
  message-age integration tests before production use.
- Store availability, CDN reachability and whitelist behavior require a dated
  operator/region field test.

### MAX command menu

The plugin registers up to 32 commands through `PATCH /me/commands`. The exact
set follows the command registry of the installed Hermes version. The current
validated installation exposes:

```text
/menu /commands /help /status /new /stop /model /compress
/sessions /resume /retry /undo /agents /whoami /queue /maxstatus
```

If a MAX client does not render the native bot menu, send `/commands` or
`/menu`: the plugin replies with the same list as ordinary text and adds
buttons. After a gateway restart, the log should contain a line like
`MAX command menu registered (N): ...`.

To verify registration without polling or model execution:

```bash
python scripts/max_commands_live_smoke.py
```

The script performs only `GET /me`, `PATCH /me/commands`, and a second
`GET /me`; it never prints the token or chat content. Run it with one polling
owner at a time so the diagnostic call is not confused with event handling.

## 8. Verification and rollback

```powershell
python -m pytest -q
```

For a live transport check, provide `MAX_BOT_TOKEN` and `MAX_CA_BUNDLE` only in
the shell environment, then run `scripts/max_live_smoke.py` without polling
when the gateway is active. Use `scripts/max_media_live_smoke.py` for outbound
image/document delivery and `scripts/max_upload_live_inspect.py` for a
redacted upload-response shape. The adapter-level
check `scripts/max_adapter_live_smoke.py` uses temporary SQLite state and a
collector instead of Hermes model execution.

Before restarting Hermes, run the loader import smoke against the current
Hermes installation. To roll back, stop the gateway, remove or retarget only
`~/.hermes/plugins/max`, then start Hermes again. Hermes core files are not
changed by this plugin.
