"""Read-only compatibility smoke for the current Hermes plugin loader.

Usage from WSL:
    python3 scripts/max_loader_smoke.py --hermes-root /home/user/.hermes/hermes-agent

The script imports the plugin as Hermes does, captures registration metadata,
and never writes to Hermes home, config, services, or the running gateway.
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
        default=Path(__file__).resolve().parents[1] / "plugins" / "max",
    )
    args = parser.parse_args()

    hermes_root = args.hermes_root.resolve()
    plugin_dir = args.plugin_dir.resolve()
    if not (hermes_root / "gateway").is_dir():
        raise SystemExit(f"Hermes root is invalid: {hermes_root}")
    if not (plugin_dir / "plugin.yaml").is_file():
        raise SystemExit(f"MAX plugin manifest is missing: {plugin_dir / 'plugin.yaml'}")

    sys.path.insert(0, str(hermes_root))
    parent = types.ModuleType("hermes_plugins")
    parent.__path__ = []  # type: ignore[attr-defined]
    sys.modules["hermes_plugins"] = parent
    name = "hermes_plugins.max_platform"
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
    required = {
        "apply_yaml_config_fn",
        "env_enablement_fn",
        "is_connected",
        "standalone_sender_fn",
    }
    missing = sorted(key for key in required if not entry.get(key))
    if missing:
        raise SystemExit(f"MAX registration hooks missing: {', '.join(missing)}")

    # The real PluginContext writes a PlatformEntry to Hermes' registry before
    # any adapter factory is instantiated. Mirror that small lifecycle step so
    # Platform("max") exercises the same dynamic-enum path as gateway startup.
    from gateway.platform_registry import PlatformEntry, platform_registry

    platform_registry.register(PlatformEntry(**entry))
    signature = inspect.signature(module.MaxAdapter.connect)
    if "is_reconnect" not in signature.parameters:
        raise SystemExit("MaxAdapter.connect lacks is_reconnect")
    try:
        from gateway.config import PlatformConfig

        module.MaxAdapter(PlatformConfig(enabled=True, token="loader-smoke-token"))
    except Exception as exc:  # noqa: BLE001 - report the contract failure clearly
        raise SystemExit(f"MaxAdapter cannot instantiate against Hermes: {exc}") from exc

    print(f"plugin_import=ok module={name}")
    print(f"platform_name={entry['name']}")
    print("adapter_instantiation=ok")
    print(f"hooks={','.join(sorted(required))}")
    print("writes_hermes_core=no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
