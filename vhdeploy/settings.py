"""Every server setting, its default, and what makes it valid.

The server does not validate its own flags: an unknown modifier is ignored, it
starts, and it runs a world nobody configured, which looks exactly like the
setting having worked. So everything is rejected here, before anything is
installed.
"""

from __future__ import annotations

import re
from typing import Any

DEFAULT_PORT = 2456
# Consecutive servers on one host are spaced out rather than packed, leaving
# room around the two ports each one binds.
PORT_STEP = 10
AGENT_PORT_OFFSET = 16000  # agent loopback port = game port + this

DEFAULTS: dict[str, Any] = {
    "server_name": "Valheim",
    "world_name": "Dedicated",
    "port": DEFAULT_PORT,
    "public": False,
    "crossplay": False,
    "admins": [],
    "preset": "",
    "modifiers": {},
    "world_keys": [],
    # 600, not Valheim's 1800. There is no on-demand save (see host/agent.py),
    # so the autosave is the only backstop against a crash, at the cost of a
    # brief stall per save: 0.6-2s was measured on a played world.
    "save_interval": 600,
    "backups": 4,
    "backup_short_interval": 7200,
    "backup_long_interval": 43200,
    "backup_enabled": True,
    "backup_time": "05:30",
    "backup_retain": 7,
    "firewall_enabled": True,
    "start_timeout": 300,
    # Windows app: start this server whenever the app opens.
    "autostart": True,
}

PRESETS = ["Normal", "Casual", "Easy", "Hard", "Hardcore", "Immersive", "Hammer"]
MODIFIERS = {
    "combat": ["veryeasy", "easy", "hard", "veryhard"],
    "deathpenalty": ["casual", "veryeasy", "easy", "hard", "hardcore"],
    "resources": ["muchless", "less", "more", "muchmore", "most"],
    "raids": ["none", "muchless", "less", "more", "muchmore"],
    "portals": ["casual", "hard", "veryhard"],
}
WORLD_KEYS = ["nobuildcost", "playerevents", "passivemobs", "nomap", "fire"]

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
WORLD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,29}$")
ADMIN_RE = re.compile(r"^[A-Za-z0-9]+_[0-9]+$")
TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


def check_slug(name: str) -> str:
    if not NAME_RE.match(name):
        raise ValueError("use lowercase letters, numbers and dashes (it names files and cloud resources)")
    return name


def check_password(password: str, server_name: str) -> str:
    # Iron Gate enforces both of these in the server, by refusing to start.
    # Catching them here turns a dead server into a sentence.
    if len(password) < 5:
        raise ValueError("the password must be at least 5 characters")
    if any(c in password for c in "\r\n"):
        raise ValueError("the password cannot contain line breaks")
    if password.lower() in server_name.lower():
        raise ValueError("Valheim refuses to start if the password appears in the server name")
    return password


def validate(cfg: dict[str, Any]) -> None:
    unknown = set(cfg) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown settings: {sorted(unknown)}")

    def need(ok: bool, message: str) -> None:
        if not ok:
            raise ValueError(message)

    need(0 < len(cfg["server_name"]) <= 64 and "\n" not in cfg["server_name"], "server_name: 1-64 characters")
    need(bool(WORLD_RE.match(cfg["world_name"])),
         "world_name: letters, numbers, spaces, dashes and underscores (30 at most)")
    need(isinstance(cfg["port"], int) and 1024 <= cfg["port"] <= 49000,
         "port: 1024-49000 (the server binds port and port + 1, and the agent a port above them)")
    need(not cfg["preset"] or cfg["preset"] in PRESETS, f"preset: one of {', '.join(PRESETS)}, or empty")
    for key, value in cfg["modifiers"].items():
        need(key in MODIFIERS, f"modifier: one of {', '.join(MODIFIERS)}")
        need(value in MODIFIERS[key], f"{key}: one of {', '.join(MODIFIERS[key])}")
    for key in cfg["world_keys"]:
        need(key in WORLD_KEYS, f"world key: one of {', '.join(WORLD_KEYS)}")
    for admin in cfg["admins"]:
        need(bool(ADMIN_RE.match(admin)), f"admin {admin!r}: use the form Steam_76561198000000000")
    need(bool(TIME_RE.match(cfg["backup_time"])), "backup_time: HH:MM, 24-hour")
    for key in ("save_interval", "backups", "backup_short_interval", "backup_long_interval",
                "backup_retain", "start_timeout"):
        need(isinstance(cfg[key], int) and cfg[key] > 0, f"{key}: a positive whole number")


def host_config(name: str, settings: dict[str, Any], ssh_port: int = 22) -> dict[str, Any]:
    """The non-secret document that lands in /etc/valheim on the game host."""
    return {
        **settings,
        "name": name,
        "instance_dir": f"/opt/valheim/instances/{name}",
        "agent_port": settings["port"] + AGENT_PORT_OFFSET,
        "ssh_port": ssh_port,
    }
