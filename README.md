# Hermes Agent MAX and VK plugins

Unofficial external platform plugins for [Hermes Agent](https://github.com/NousResearch/hermes-agent):

- MAX Bot API v2 through HTTPS Webhook or development Long Polling;
- VK Community Long Poll with direct HTTP transport.

The code stays outside the Hermes core checkout, so Hermes can be upgraded or
rolled back independently.

## Status

This is an experimental community project. The latest local implementation
verification passed 133 deterministic tests and loader checks, but live
acceptance still requires disposable MAX/VK credentials and a real test
user/community.

The project does not promise universal MAX/VK availability, whitelist behavior,
or regulator/operator connectivity. Measure those separately for the target
region, operator, device, and incident mode.

Implemented areas include:

- MAX Bot API v2 client, text routing, Webhook/Long Polling, callback keyboards,
  model picker, bounded media transport, and TLS validation;
- VK Community Long Poll, durable marker and deduplication, direct API client,
  callbacks, typing, message edits, pairing, allowlists, and bounded media;
- Hermes `plugin.yaml` manifests, platform adapters, standalone sender hooks,
  contract tests, and loader smoke scripts.

Known release gates:

- live MAX callback and inbound/outbound media acceptance;
- live VK Community Long Poll, callback, and media acceptance;
- compatibility checks against the installed Hermes version after upgrades;
- real network availability in the intended region and operator environment.

## Install

Clone this repository, then link or copy only the plugin directories into the
Hermes user plugin directory. Do not copy them into the Hermes source tree.

PowerShell example:

```powershell
$repo = (Get-Location).Path
New-Item -ItemType Junction `
  -Path "$HOME\.hermes\plugins\max" `
  -Target "$repo\plugins\max"
New-Item -ItemType Junction `
  -Path "$HOME\.hermes\plugins\vk" `
  -Target "$repo\plugins\vk"
```

The plugin manifest must remain named `plugin.yaml`. Third-party plugins are
opt-in in Hermes; enable only the platform you have reviewed:

```bash
hermes plugins list
hermes plugins enable max-platform
hermes plugins enable vk
```

Run the loader smoke test after changing Hermes and before restarting a
production gateway.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m compileall -q plugins tests scripts
```

Live checks require secrets supplied only through the environment. Never put
real tokens, cookies, databases, logs, or production configuration in Git:

```bash
python scripts/max_live_smoke.py --user-id 100000001 --poll-seconds 30
python scripts/max_adapter_live_smoke.py --seconds 8
python scripts/vk_loader_smoke.py --hermes-root /home/user/.hermes/hermes-agent
```

The live scripts are diagnostics, not a production readiness claim.

## Configuration

MAX requires at least:

```env
MAX_BOT_TOKEN=<token from MAX for Business>
MAX_ALLOWED_USERS=<numeric MAX user IDs separated by commas>
```

VK requires at least:

```env
VK_GROUP_TOKEN=<community access token>
VK_GROUP_ID=<numeric VK community ID>
VK_ALLOWED_USERS=<numeric VK user IDs separated by commas>
```

Both adapters default to restrictive allowlist behavior. Review the complete
environment reference in [MAX setup](docs/en/max-setup.md) and [VK setup](docs/en/vk-setup.md).
Russian instructions are available in [MAX setup](docs/ru/max-setup.md) and
[VK setup](docs/ru/vk-setup.md).

## Security boundary

The adapters process third-party network input and should be enabled only after
review. The implementation uses allowlists, bounded media reads, token
redaction, HTTPS host checks, callback expiry/binding, and TLS verification.
These controls reduce risk but do not replace deployment-specific review.

Report security issues privately according to [SECURITY.md](SECURITY.md). Do not
open a public issue containing credentials, private messages, or exploit details.

## License

MIT. See [LICENSE](LICENSE).