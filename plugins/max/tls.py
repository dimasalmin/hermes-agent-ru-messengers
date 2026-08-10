"""TLS policy for MAX API connections."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


def tls_verify_from_env(env: Mapping[str, str] | None = None) -> bool | str:
    """Return a verified httpx CA setting without allowing insecure mode.

    ``True`` delegates to the host trust store.  ``MAX_CA_BUNDLE`` can point
    to a deployment-managed PEM bundle containing the host roots plus the
    current official Russian trust chain required by MAX.
    """

    values = env if env is not None else os.environ
    raw = str(values.get("MAX_CA_BUNDLE", "")).strip()
    if not raw or raw.lower() in {"system", "default", "true", "1", "yes"}:
        return True
    if raw.lower() in {"false", "0", "no", "insecure", "none"}:
        raise ValueError("MAX TLS verification cannot be disabled")
    path = Path(raw).expanduser()
    if not path.is_file():
        raise ValueError(f"MAX CA bundle does not exist: {path}")
    try:
        pem = path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"MAX CA bundle cannot be read as ASCII PEM: {path}") from exc
    begin = "-----BEGIN CERTIFICATE-----"
    end = "-----END CERTIFICATE-----"
    if begin not in pem or end not in pem:
        raise ValueError(f"MAX CA bundle is not a PEM certificate bundle: {path}")
    for block in pem.split(begin)[1:]:
        body = block.split(end, 1)[0].strip()
        if not body:
            raise ValueError(f"MAX CA bundle contains an empty certificate: {path}")
    return str(path)
