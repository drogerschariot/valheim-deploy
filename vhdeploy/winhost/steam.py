"""Installing and updating the Valheim dedicated server with SteamCMD."""

from __future__ import annotations

import io
import os
import time
import urllib.request
import zipfile
from typing import Any

from .. import output
from . import IS_WINDOWS, game_dir, steam_dir

STEAMCMD_URL = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip"
SERVER_APP_ID = "896660"


def steamcmd(ui: Any) -> str:
    exe = steam_dir() / "steamcmd.exe"
    if not exe.exists():
        ui.say("Downloading SteamCMD, Valve's tool for installing game servers...")
        steam_dir().mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(STEAMCMD_URL, timeout=120) as res:
            data = res.read()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(steam_dir())
    return str(exe)


def install_game(ui: Any) -> None:
    """Install or update the server. About 1 GB the first time."""
    if os.environ.get("VALHEIM_SKIP_STEAM"):
        ui.say("Skipping the Steam download (VALHEIM_SKIP_STEAM is set).")
        return
    if not IS_WINDOWS:
        raise SystemExit("Installing the game this way only works on Windows.")
    exe = steamcmd(ui)
    game_dir().mkdir(parents=True, exist_ok=True)
    argv = [exe, "+force_install_dir", str(game_dir()), "+login", "anonymous",
            "+app_update", SERVER_APP_ID, "validate", "+quit"]
    # SteamCMD updates itself on its first run and often exits non-zero
    # doing so, so a failed first attempt is normal.
    for attempt in range(1, 4):
        ui.say(f"Downloading the Valheim server from Steam (attempt {attempt} of 3). "
               "The first time is about 1 GB...")
        code = output.stream(argv)
        if code == 0 and (game_dir() / "valheim_server.exe").exists():
            ui.ok("The Valheim server is installed and up to date.")
            return
        time.sleep(5)
    raise SystemExit("SteamCMD could not install the Valheim server. Check your internet connection "
                     "and free disk space (about 2 GB), then try Update game.")
