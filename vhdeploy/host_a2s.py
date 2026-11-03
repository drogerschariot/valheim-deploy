"""Loads the A2S client from host/, so there is one copy of it."""

import importlib.util

from .ssh import HOST_DIR

_spec = importlib.util.spec_from_file_location("valheim_host_a2s", HOST_DIR / "a2s.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
query = _module.query
