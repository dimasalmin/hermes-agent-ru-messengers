"""Exercise MaxAdapter against a disposable MAX bot without model execution.

The adapter uses the real Hermes BasePlatformAdapter import, but its message
handler is replaced with a collector. This validates connect, MAX polling,
normalization, allowlist and disconnect without touching the active gateway.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace

# Run against the selected Hermes release, independent of the script path.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HERMES_ROOT = Path(
    os.environ.get("HERMES_ROOT", "/home/user/.hermes/hermes-agent-current")
).expanduser()
sys.path.insert(0, str(HERMES_ROOT))
sys.path.insert(0, str(REPOSITORY_ROOT))


def _load_plugin():
    parent = types.ModuleType("hermes_plugins")
    parent.__path__ = []  # type: ignore[attr-defined]
    sys.modules["hermes_plugins"] = parent
    name = "hermes_plugins.max_platform"
    spec = importlib.util.spec_from_file_location(
        name,
        REPOSITORY_ROOT / "plugins" / "max" / "__init__.py",
        submodule_search_locations=[str(REPOSITORY_ROOT / "plugins" / "max")],
    )
    if spec is None or spec.loader is None:
        raise SystemExit("MAX plugin import spec could not be created")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MAX_PLUGIN = _load_plugin()

from gateway.config import PlatformConfig
from gateway.platform_registry import PlatformEntry, platform_registry


def _register_platform() -> None:
    context = SimpleNamespace()
    context.register_platform = lambda **kwargs: platform_registry.register(PlatformEntry(**kwargs))
    MAX_PLUGIN.register(context)


async def _run(seconds: float) -> int:
    token = os.environ.get("MAX_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("MAX_BOT_TOKEN is required")
    _register_platform()
    with tempfile.TemporaryDirectory(prefix="hermes-max-live-") as directory:
        state = Path(directory)
        config = PlatformConfig(
            enabled=True,
            token=token,
            extra={
                "api_base_url": os.environ.get("MAX_API_BASE_URL", ""),
                "ca_bundle": os.environ.get("MAX_CA_BUNDLE", ""),
                "polling_timeout": min(int(os.environ.get("MAX_POLLING_TIMEOUT", "5")), 90),
                "inbox_path": str(state / "inbox.sqlite3"),
                "marker_path": str(state / "marker.sqlite3"),
                "target_path": str(state / "targets.sqlite3"),
            },
        )
        adapter = MAX_PLUGIN.MaxAdapter(config)
        events: list[dict[str, str]] = []

        async def collect(event) -> None:
            source = event.source
            events.append(
                {
                    "message_id": str(event.message_id or ""),
                    "user_id": str(getattr(source, "user_id", "")),
                    "chat_id": str(getattr(source, "chat_id", "")),
                    "chat_type": str(getattr(source, "chat_type", "")),
                    "text_length": str(len(event.text or "")),
                }
            )

        adapter.handle_message = collect  # type: ignore[method-assign]
        connected = await adapter.connect(is_reconnect=False)
        print(f"adapter_connected={connected}")
        if connected:
            await asyncio.sleep(max(0.1, seconds))
        await adapter.disconnect()
        print(f"events={len(events)}")
        for event in events:
            print(
                "event "
                + " ".join(f"{key}={value}" for key, value in event.items())
            )
    return 0 if connected else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=8.0)
    args = parser.parse_args()
    return asyncio.run(_run(args.seconds))


if __name__ == "__main__":
    raise SystemExit(main())
