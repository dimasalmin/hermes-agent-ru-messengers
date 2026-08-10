"""Export the VK directory plugin as a standalone Hermes plugin repository."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


PLUGIN_FILES = (
    "__init__.py",
    "adapter.py",
    "cli.py",
    "client.py",
    "common.py",
    "formatting.py",
    "interactive.py",
    "media.py",
    "models.py",
    "plugin.yaml",
    "rate_limit.py",
    "state.py",
    "LICENSE",
    "README.md",
)


def export_plugin(source: Path, destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for relative in PLUGIN_FILES:
        source_path = source / relative
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        target_path = destination / relative
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
        copied.append(target_path)
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "plugins" / "vk",
    )
    args = parser.parse_args()
    copied = export_plugin(args.source.resolve(), args.destination.resolve())
    print(f"exported={len(copied)} files")
    print(f"destination={args.destination.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
