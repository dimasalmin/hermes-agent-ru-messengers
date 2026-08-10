"""Read-only loader compatibility smoke for the VK external plugin.

This imports the plugin against a selected Hermes checkout, captures its
registration metadata and instantiates the adapter without a network request.
It never writes into Hermes core or starts the gateway.
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import sys
import types
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-root", type=Path, required=True)
    parser.add_argument(
        "--plugin-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "plugins" / "vk",
    )
    args = parser.parse_args()
    hermes_root = args.hermes_root.resolve()
    plugin_dir = args.plugin_dir.resolve()
    repo_root = plugin_dir.parents[1]
    if not (hermes_root / "gateway").is_dir():
        raise SystemExit(f"Hermes root is invalid: {hermes_root}")
    if not (plugin_dir / "plugin.yaml").is_file():
        raise SystemExit(f"VK plugin manifest is missing: {plugin_dir / 'plugin.yaml'}")

    sys.path.insert(0, str(hermes_root))
    sys.path.insert(0, str(repo_root))
    parent = types.ModuleType("hermes_plugins")
    parent.__path__ = []  # type: ignore[attr-defined]
    sys.modules["hermes_plugins"] = parent
    name = "hermes_plugins.vk_platform"
    spec = importlib.util.spec_from_file_location(
        name,
        plugin_dir / "__init__.py",
        submodule_search_locations=[str(plugin_dir)],
    )
    if spec is None or spec.loader is None:
        raise SystemExit("Could not create plugin import spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

    context = types.SimpleNamespace()
    context.register_platform = lambda **kwargs: setattr(context, "entry", kwargs)
    module.register(context)
    entry = context.entry
    required = {"env_enablement_fn", "is_connected", "standalone_sender_fn"}
    missing = sorted(key for key in required if not entry.get(key))
    if missing:
        raise SystemExit(f"VK registration hooks missing: {', '.join(missing)}")

    from gateway.platform_registry import PlatformEntry, platform_registry

    platform_registry.register(PlatformEntry(**entry))
    from gateway.config import PlatformConfig

    signature = inspect.signature(module.VkAdapter.connect)
    if "is_reconnect" not in signature.parameters:
        raise SystemExit("VkAdapter.connect lacks is_reconnect")
    module.VkAdapter(PlatformConfig(enabled=True, token="loader-smoke-token", extra={"group_id": 123}))
    print(f"plugin_import=ok module={name}")
    print(f"platform_name={entry['name']}")
    print("adapter_instantiation=ok")
    print(f"hooks={','.join(sorted(required))}")
    print("writes_hermes_core=no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
