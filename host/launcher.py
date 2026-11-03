#!/usr/bin/env python3
"""Starts the Valheim dedicated server for one instance.

The arguments are built as a list and handed straight to exec, never through a
shell, so a server name like `Bob's "World" $(x)` is just a name.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

STEAM_GAME_APP_ID = "892970"


def build_argv(cfg: dict[str, Any], password: str, binary: str) -> list[str]:
    argv = [
        binary,
        "-name", cfg["server_name"],
        "-port", str(cfg["port"]),
        "-world", cfg["world_name"],
        "-password", password,
        "-savedir", str(Path(cfg["instance_dir"]) / "data"),
        "-public", "1" if cfg["public"] else "0",
        "-saveinterval", str(cfg["save_interval"]),
        "-backups", str(cfg["backups"]),
        "-backupshort", str(cfg["backup_short_interval"]),
        "-backuplong", str(cfg["backup_long_interval"]),
    ]
    if cfg.get("crossplay"):
        argv.append("-crossplay")
    # The order is not cosmetic: a preset overwrites every modifier set before
    # it, so -preset comes first or it silently wins and the specific values
    # are lost. Each modifier needs its own flag; there is no comma form.
    if cfg.get("preset"):
        argv += ["-preset", cfg["preset"]]
    for key, value in sorted(cfg.get("modifiers", {}).items()):
        argv += ["-modifier", key, value]
    for key in cfg.get("world_keys", []):
        argv += ["-setkey", key]
    return argv


def game_env(cfg: dict[str, Any]) -> dict[str, str]:
    server_dir = str(Path(cfg["instance_dir"]) / "server")
    env = dict(os.environ)
    env.pop("VALHEIM_SERVER_PASSWORD", None)
    env.pop("VALHEIM_AGENT_TOKEN", None)
    env.pop("VALHEIM_CONFIG_JSON", None)
    # The dedicated server is Steam app 896660 but must advertise itself as the
    # game. Without this it starts and then fails to register with Steam.
    env["SteamAppId"] = STEAM_GAME_APP_ID
    previous = env.get("LD_LIBRARY_PATH")
    env["LD_LIBRARY_PATH"] = f"{server_dir}/linux64" + (f":{previous}" if previous else "")
    env["HOME"] = cfg["instance_dir"]
    return env


def main() -> None:
    cfg = json.loads(Path(f"/etc/valheim/{sys.argv[1]}.json").read_text(encoding="utf-8"))
    password = os.environ.get("VALHEIM_SERVER_PASSWORD", "")
    if not password:
        raise SystemExit("VALHEIM_SERVER_PASSWORD is unset; refusing to start.")
    server_dir = Path(cfg["instance_dir"]) / "server"
    os.chdir(server_dir)
    binary = str(server_dir / "valheim_server.x86_64")
    # exec matters as much as KillSignal=SIGINT does. systemd signals the
    # unit's main process; without exec that is this interpreter, which would
    # take the SIGINT and leave the server to be SIGKILLed with an unwritten
    # world. The password is visible in `ps` for local users: Valheim accepts
    # it no other way.
    os.execve(binary, build_argv(cfg, password, binary), game_env(cfg))


if __name__ == "__main__":
    main()
