from __future__ import annotations

import argparse

from plugins.vk.cli import handle_cli, setup_cli
from plugins.vk.state import VkStateStore


def test_vk_cli_pairing_issue_and_revoke(tmp_path, capsys):
    parser = argparse.ArgumentParser()
    setup_cli(parser)
    args = parser.parse_args(
        ["pairing", "issue", "--user-id", "100000001", "--state-path", str(tmp_path / "vk.sqlite3")]
    )
    assert handle_cli(args) == 0
    output = capsys.readouterr().out.strip()
    assert output.isdigit() and len(output) == 6

    store = VkStateStore(str(tmp_path / "vk.sqlite3"))
    try:
        assert store.is_paired("100000001") is False
    finally:
        store.close()

    revoke = parser.parse_args(
        ["pairing", "revoke", "--user-id", "100000001", "--state-path", str(tmp_path / "vk.sqlite3")]
    )
    assert handle_cli(revoke) == 0
    assert "revoked" in capsys.readouterr().out.lower()


def test_vk_cli_status_does_not_print_credentials(tmp_path, capsys):
    parser = argparse.ArgumentParser()
    setup_cli(parser)
    args = parser.parse_args(["status", "--state-path", str(tmp_path / "vk.sqlite3")])
    assert handle_cli(args) == 0
    output = capsys.readouterr().out
    assert "schema_version=1" in output
    assert "token" not in output.lower()


def test_vk_cli_validate_checks_local_configuration_without_printing_token(monkeypatch, capsys):
    parser = argparse.ArgumentParser()
    setup_cli(parser)
    monkeypatch.setenv("VK_GROUP_TOKEN", "secret-token")
    monkeypatch.setenv("VK_GROUP_ID", "240751855")
    args = parser.parse_args(["validate"])
    assert handle_cli(args) == 0
    output = capsys.readouterr().out
    assert "configured=true" in output
    assert "secret-token" not in output
