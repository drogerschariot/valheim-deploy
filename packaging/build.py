#!/usr/bin/env python3
"""Builds the single-file app with PyInstaller. Usage: python packaging/build.py NAME"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
name = sys.argv[1] if len(sys.argv) > 1 else "valheim-deploy"
subprocess.run([
    sys.executable, "-m", "PyInstaller", "--onefile", "--console", "--clean", "--noconfirm",
    "--name", name, "--paths", str(ROOT),
    "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"), "--specpath", str(ROOT / "build"),
    # Everything a game host receives, and the dashboard's own pages.
    "--add-data", f"{ROOT / 'host'}:host",
    "--add-data", f"{ROOT / 'vhdeploy' / 'static'}:vhdeploy/static",
    str(ROOT / "packaging" / "entry.py"),
], check=True)
