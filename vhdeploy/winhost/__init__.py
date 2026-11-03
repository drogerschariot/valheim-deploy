"""Running Valheim servers on this Windows PC, inside the app itself.

The Linux pieces in host/ assume systemd or Docker. Here the app supervises
valheim_server.exe directly, and answers the same JSON API as host/agent.py
in-process, so the dashboard does not need to know the difference.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .. import paths, registry

# host/ is plain scripts, not a package. The agent's parsers and collectors
# are reused rather than copied.
if str(paths.host_dir()) not in sys.path:
    sys.path.insert(0, str(paths.host_dir()))

IS_WINDOWS = sys.platform == "win32"
PROVIDER = "windows"


def root() -> Path:
    return registry.home()


def steam_dir() -> Path:
    return root() / "steamcmd"


def game_dir() -> Path:
    """One game install shared by every server. They must all match the
    players' game version anyway, so separate copies would only cost disk."""
    return root() / "game"


def instance_dir(name: str) -> Path:
    return root() / "instances" / name


def instance_config(rec: dict[str, Any]) -> dict[str, Any]:
    """The same shape host/agent.py reads from /etc/valheim/NAME.json."""
    return {**rec["settings"], "name": rec["name"], "instance_dir": str(instance_dir(rec["name"]))}


_manager: Any = None


def get_manager() -> Any:
    """The running app's ServerManager. Created on first use."""
    global _manager
    if _manager is None:
        from .manager import ServerManager
        _manager = ServerManager()
    return _manager


def set_manager(value: Any) -> None:
    global _manager
    _manager = value
