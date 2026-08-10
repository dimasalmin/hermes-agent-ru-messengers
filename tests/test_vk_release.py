from __future__ import annotations

from pathlib import Path

from scripts.export_vk_plugin import PLUGIN_FILES, export_plugin


def test_vk_export_contains_only_standalone_plugin_files(tmp_path):
    source = Path(__file__).parents[1] / "plugins" / "vk"
    destination = tmp_path / "hermes-vk-plugin"
    copied = export_plugin(source, destination)

    assert len(copied) == len(PLUGIN_FILES)
    assert (destination / "plugin.yaml").is_file()
    assert (destination / "__init__.py").is_file()
    assert not (destination / "tests").exists()
