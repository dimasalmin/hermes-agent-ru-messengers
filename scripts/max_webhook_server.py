"""Run the optional MAX Webhook ingress outside the Hermes gateway.

The process only validates and durably stores updates. Hermes consumes the
same SQLite inbox, so a restart between HTTP ACK and model processing does not
drop an accepted event.
"""

from __future__ import annotations

import os
from pathlib import Path

from plugins.max.webhook import MaxWebhookReceiver
from plugins.max.webhook_app import MaxWebhookASGI, MaxWebhookIngress


def main() -> None:
    secret = os.environ.get("MAX_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise SystemExit("MAX_WEBHOOK_SECRET is required")
    home = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
    inbox = os.environ.get("MAX_INBOX_PATH", str(home / "max" / "webhook-inbox.sqlite3"))
    receiver = MaxWebhookReceiver(secret, inbox_path=inbox)
    app = MaxWebhookASGI(MaxWebhookIngress(receiver))

    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("Install the webhook extra: pip install '.[webhook]'") from exc

    uvicorn.run(
        app,
        host=os.environ.get("MAX_WEBHOOK_HOST", "127.0.0.1"),
        port=int(os.environ.get("MAX_WEBHOOK_PORT", "8080")),
        log_level=os.environ.get("MAX_WEBHOOK_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
