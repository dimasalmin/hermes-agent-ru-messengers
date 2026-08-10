# VK setup for Hermes Agent

## 1. Create a community token

1. Create or open a VK community.
2. Enable community messages.
3. Enable Community Long Poll in API settings.
4. Create a community access token with messages permission. Enable photo and
   document permissions if media egress is required.
5. Record the numeric community ID from the community URL.

Official references:

- [Community Messages getting started](https://dev.vk.com/en/api/community-messages/getting-started)
- [messages.send](https://dev.vk.com/method/messages.send)
- [messages.sendMessageEventAnswer](https://dev.vk.com/method/messages.sendMessageEventAnswer)
- [photos.getMessagesUploadServer](https://dev.vk.com/method/photos.getMessagesUploadServer)
- [docs.getMessagesUploadServer](https://dev.vk.com/method/docs.getMessagesUploadServer)

## 2. Install as an external plugin

```bash
pip install -e ".[vk]"
ln -s "$(pwd)/plugins/vk" ~/.hermes/plugins/vk
```

Keep this repository outside the Hermes checkout. Do not copy files into
Hermes `gateway/` or its managed release directory.

For a standalone release, export the plugin directory and publish the exported
directory as its own repository:

```bash
python scripts/export_vk_plugin.py --destination ../hermes-vk-plugin
```

The destination contains the plugin root (`plugin.yaml`, `__init__.py` and
transport modules) and does not include MAX files.

## 3. Configure

```env
VK_GROUP_TOKEN=vk1.a...
VK_GROUP_ID=123456789
VK_ALLOWED_USERS=100000001
VK_STATE_PATH=/home/user/.hermes/vk/state.sqlite3
# Optional; defaults are secure allowlists.
VK_DM_POLICY=allowlist
VK_GROUP_POLICY=allowlist
# Optional; default is true for group chats.
VK_REQUIRE_MENTION=true
```

Start Hermes normally. The plugin obtains a Long Poll server, persists its
marker, and reconnects after transient errors. A stale Long Poll key or marker
causes a fresh `groups.getLongPollServer` initialization.

## 4. Operational notes

- A DM uses `peer_id == user_id`.
- A multi-user chat uses `peer_id = 2_000_000_000 + chat_id`.
- Group messages require an allowlist match and, by default, an explicit bot
  mention. Set `VK_REQUIRE_MENTION=false` only for an explicitly controlled chat.
- Do not enable `VK_ALLOW_ALL_USERS` on an internet-facing bot without a separate
  threat review.
- Incoming attachments are HTTPS-only, host-checked and size-bounded.
- Callback tokens are not commands. The adapter checks the VK user and peer
  before resolving a stored action.
- `VK_DM_POLICY` and `VK_GROUP_POLICY` accept `allowlist`, `pairing`, `open` or
  `disabled`. `open` is an explicit operator choice; the safe default remains
  `allowlist`.
- To enable pairing, set `VK_DM_POLICY=pairing`, issue a code as the operator,
  then ask the user to send `/pair <code>`:

  ```bash
  hermes vk pairing issue --user-id 100000001
  ```

  Pairing codes are short-lived, stored only as hashes and bound to the VK
  user. They are not printed to logs.
- One gateway process owns the configured state path through a SQLite lease;
  a second poller for the same state path exits instead of consuming events.

## 5. Diagnostics

```bash
python -m pytest -q
hermes doctor
hermes vk validate
hermes vk validate --live
```

If the bot is silent, check token permissions, Community Messages, Long Poll,
`VK_GROUP_ID`, the allowlist and the plugin loader log. Live production
acceptance must be performed with a disposable test token and a real VK user.
