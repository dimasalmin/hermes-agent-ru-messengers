# Changelog

## 0.1.0 - 2026-08-09

- First public packaging of the external MAX and VK Hermes platform plugins.
- Added deterministic contract coverage for API clients, callbacks, media,
  allowlists, durable state, and standalone VK export.
- Local verification: 133 tests passed; live MAX/VK acceptance remains open.
- Added public installation, security, contribution, and CI documentation.

## Unreleased

- Added MAX inline keyboards for Hermes clarify prompts, exec approvals and
  slash confirmations.
- Added a two-step `/model` picker for provider and model selection.
- Added official `/answers` callback acknowledgement, user/chat binding,
  expiry and single-use replay protection.
- Added bounded inbound media caching, outbound `MEDIA:` uploads, standalone
  media delivery and `MAX_MEDIA_MAX_BYTES` configuration.
- Kept the feature in the external plugin; Hermes core remains unchanged.

## 0.2.0 - 2026-08-08

- Rebuilt MAX integration as an external Hermes plugin using the current v0.20.0
  adapter contract.
- Added direct MAX Bot API v2 client, Long Polling marker persistence, Webhook
  secret validation, durable SQLite inbox and optional standalone ASGI ingress.
- Added DM/group allowlist policy, MAX reply `link.mid`, persistent target type,
  TLS fail-closed checks and global/per-dialog rate limiting.
- Marked live media acceptance, streaming edit coalescing, health metrics and
  live no-VPN validation as remaining release gates.

This release does not modify Hermes core and is not a claim of universal MAX
availability during regional network restrictions.
