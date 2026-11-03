"""Where bundled files live, from source or from a packaged single-file app."""

from __future__ import annotations

import sys
from pathlib import Path


def bundle_root() -> Path:
    # PyInstaller unpacks a one-file app to a temp directory named here.
    frozen = getattr(sys, "_MEIPASS", None)
    return Path(frozen) if frozen else Path(__file__).resolve().parent.parent


def host_dir() -> Path:
    return bundle_root() / "host"


def static_dir() -> Path:
    root = bundle_root()
    return root / "vhdeploy" / "static"


def is_frozen() -> bool:
    return hasattr(sys, "_MEIPASS")


def command() -> str:
    """How the user should type this program in the messages we show them."""
    return Path(sys.executable).name if is_frozen() else "./valheim"
