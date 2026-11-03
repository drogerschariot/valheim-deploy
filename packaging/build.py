#!/usr/bin/env python3
"""Builds the apps with PyInstaller.

  python packaging/build.py windows-app   the Windows app: a folder for the installer
  python packaging/build.py NAME          the single-file command-line app
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
name = sys.argv[1] if len(sys.argv) > 1 else "valheim-deploy"
common = [
    sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "--paths", str(ROOT),
    "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"), "--specpath", str(ROOT / "build"),
    # Everything a game host receives (the agent's parsers are reused on
    # Windows too), and the dashboard's own pages.
    "--add-data", f"{ROOT / 'host'}:host",
    "--add-data", f"{ROOT / 'vhdeploy' / 'static'}:vhdeploy/static",
]

if name == "windows-app":
    icon = ROOT / "build" / "ValheimDeploy.ico"
    icon.parent.mkdir(exist_ok=True)
    subprocess.run([sys.executable, str(ROOT / "packaging" / "make_icon.py"), str(icon)], check=True)
    subprocess.run([
        *common, "--onedir", "--windowed", "--name", "ValheimDeploy", "--icon", str(icon),
        "--paths", str(ROOT / "host"),
        # Loaded by name at run time, so PyInstaller cannot see them.
        "--hidden-import", "pystray._win32", "--hidden-import", "agent", "--hidden-import", "launcher",
        "--hidden-import", "a2s",
        str(ROOT / "packaging" / "winapp_entry.py"),
    ], check=True)
else:
    subprocess.run([*common, "--onefile", "--console", "--name", name,
                    str(ROOT / "packaging" / "entry.py")], check=True)
