from __future__ import annotations

from pathlib import Path


def test_max_plugin_does_not_depend_on_sibling_repo_package() -> None:
    source = Path("plugins/max/adapter.py").read_text(encoding="utf-8")

    assert "from plugins._ru_common" not in source
