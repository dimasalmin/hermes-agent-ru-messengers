# MAX Hermes plugin

This directory is intentionally self-contained because Hermes loads external
plugins as `hermes_plugins.<slug>`. Keep internal imports relative and do not
depend on sibling packages from this repository.

The plugin targets MAX Bot API v2 at `platform-api2.max.ru` and uses the public
Hermes platform adapter contract. It currently exposes the text MVP, development
Long Polling, Webhook receiver/queue primitives, allowlist policy, API errors,
and TLS verification.

Install this directory as `~/.hermes/plugins/max/` with the lowercase
`plugin.yaml` manifest. Do not install it inside the Hermes source tree.

See the Russian setup guide at `../../docs/ru/max-setup.md` and the public
English setup guide at `../../docs/en/max-setup.md`.
