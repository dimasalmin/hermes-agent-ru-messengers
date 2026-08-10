# Hermes Agent VK plugin

This plugin connects Hermes Agent to a VK community bot through the official
Community Long Poll API. It is an external plugin: Hermes core is not patched,
so the plugin can be upgraded independently of Hermes releases.

## Implemented surface

- Direct `httpx` transport for VK API and Community Long Poll.
- Durable schema-versioned `ts`, Long Poll server/key, poll lease, pairing and
  message deduplication in SQLite.
- DM/group routing through the shared allowlist policy.
- 4096-character chunking, `messages.edit`, typing activity and standalone send.
- VK `format_data` rendering for a bounded Markdown subset, with UTF-16 offsets.
- Generic inline/chat keyboard builder for callback, text, open-link, location
  and Mini App actions, with VK limit validation.
- Native inline callback keyboards for clarify, dangerous-command approval and
  slash confirmation. Payloads are opaque, short-lived, single-use and bound
  to the VK user and peer.
- Bounded HTTPS-only inbound photo, voice and document downloads into Hermes'
  media cache, plus outbound photo/document/audio uploads.

Operator commands are registered as `hermes vk ...` when the running Hermes
version exposes the plugin CLI hook. Pairing codes are issued by the operator,
stored as hashes, and redeemed by the user with `/pair <code>`.

## Required environment

```env
VK_GROUP_TOKEN=<community access token>
VK_GROUP_ID=<numeric community id>
VK_ALLOWED_USERS=<comma-separated VK user ids>
```

The full setup, permissions, group behavior and diagnostics are in
`../../docs/ru/vk-setup.md` and `../../docs/en/vk-setup.md`. Live acceptance
and regional connectivity remain separate release gates.
