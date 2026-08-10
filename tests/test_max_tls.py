from __future__ import annotations

import pytest

from plugins.max.tls import tls_verify_from_env


def test_tls_defaults_to_system_verification() -> None:
    assert tls_verify_from_env({}) is True


def test_tls_accepts_existing_explicit_bundle(tmp_path) -> None:
    bundle = tmp_path / "max-ca.pem"
    bundle.write_text(
        "-----BEGIN CERTIFICATE-----\n" + "A" * 32 + "\n-----END CERTIFICATE-----\n",
        encoding="ascii",
    )

    assert tls_verify_from_env({"MAX_CA_BUNDLE": str(bundle)}) == str(bundle)


def test_tls_rejects_disabled_verification() -> None:
    with pytest.raises(ValueError, match="cannot be disabled"):
        tls_verify_from_env({"MAX_CA_BUNDLE": "false"})


def test_tls_rejects_missing_bundle() -> None:
    with pytest.raises(ValueError, match="does not exist"):
        tls_verify_from_env({"MAX_CA_BUNDLE": "missing.pem"})


def test_tls_rejects_empty_pem_bundle(tmp_path) -> None:
    bundle = tmp_path / "empty.pem"
    bundle.write_text(
        "-----BEGIN CERTIFICATE-----\n-----END CERTIFICATE-----\n", encoding="ascii"
    )

    with pytest.raises(ValueError, match="empty"):
        tls_verify_from_env({"MAX_CA_BUNDLE": str(bundle)})
