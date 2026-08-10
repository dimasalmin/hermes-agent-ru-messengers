# MAX Interactive Controls

The MAX plugin implements the three native Hermes interactive hooks that are
useful in a chat session:

- `send_clarify`: one callback button per option plus `Other` for typed input;
- `send_exec_approval`: Once, Session, Always and Deny where enabled by Hermes;
- `send_slash_confirm`: Once, Always and Cancel.
- `send_model_picker`: provider selection followed by model selection and the
  existing Hermes `on_model_selected` callback.

## Callback lifecycle

1. The adapter sends an `inline_keyboard` attachment through `POST /messages`.
2. MAX sends a `message_callback` update when the user taps a button.
3. The adapter checks the MAX allowlist and consumes an opaque callback token.
4. The token must match the original user and chat, be within its TTL, and not
   have been consumed already.
5. The adapter resolves the corresponding Hermes primitive and acknowledges the
   click with `POST /answers?callback_id=...`, replacing the prompt message and
   removing its buttons.

The model picker uses the same flow for navigation: selecting a provider
replaces the provider keyboard with model buttons; selecting a model invokes
Hermes' callback and replaces the picker with the result.

Callback state is process-local by design. A gateway restart invalidates pending
buttons, because persisting a token without the live Hermes wait primitive could
produce a misleading approval prompt. The default TTL is 600 seconds and can be
changed with `MAX_CALLBACK_TTL_SECONDS`.

## Groups and fallback

Hermes currently supplies generic control metadata without a sender identity for
some group prompts. The adapter refuses native buttons in that case instead of
letting another authorized group member approve the request. Hermes then uses
its normal text fallback. Direct messages are user-bound automatically because
MAX addresses them by `user_id`.

## Verification

Run the unit suite before installing the plugin:

```powershell
python -m pytest -q
```

The live acceptance test must cover a clarify choice, `Other` followed by typed
text, an approval button, a repeated click, and a click from a non-allowlisted
MAX account. Do not place the bot token in this repository or in test fixtures.
