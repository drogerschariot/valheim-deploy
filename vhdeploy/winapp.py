"""The Windows app: double-click it, and the dashboard opens with your servers.

One process with no console window. It runs every server on this PC, serves
the dashboard on 127.0.0.1, sits in the system tray, and shows the dashboard
in an Edge app window. Closing that window leaves the servers running;
Quit in the tray stops them, saving each world first.

Usage:
  ValheimDeploy.exe                       open the dashboard (start the app if needed)
  ValheimDeploy.exe --quit                stop a running app, saving every world
  ValheimDeploy.exe --no-window           start without opening a window (CI, autostart)
  ValheimDeploy.exe --version
  ValheimDeploy.exe --send-ctrl-c PID     internal: see winhost/ctrlc.py
  ValheimDeploy.exe --firewall add|remove NAME PORT   internal: run as admin
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path

PORT = 8456


def edge() -> str | None:
    """Microsoft Edge, which every Windows 10 and 11 PC has."""
    if sys.platform != "win32":
        return None
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe") as key:
                path = winreg.QueryValue(key, None)
                if path and Path(path).exists():
                    return path
        except OSError:
            pass
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base and (Path(base) / "Microsoft/Edge/Application/msedge.exe").exists():
            return str(Path(base) / "Microsoft/Edge/Application/msedge.exe")
    return None


def open_window(url: str) -> None:
    """An app window rather than a browser tab: no address bar, its own
    taskbar entry, and its own profile so it remembers its size."""
    from . import registry
    browser = edge()
    if browser:
        subprocess.Popen([browser, f"--app={url}", f"--user-data-dir={registry.home() / 'window'}",
                          "--no-first-run", "--window-size=1280,880"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url)


def login_url() -> str:
    from . import dashboard
    return f"http://127.0.0.1:{PORT}/?login={dashboard.dashboard_token()}"


def quit_running() -> int:
    """Ask the running app to quit, and wait until every world is saved."""
    from . import dashboard
    if not dashboard.already_running(PORT):
        return 0
    token = dashboard.dashboard_token()
    base = f"http://127.0.0.1:{PORT}"
    request = urllib.request.Request(f"{base}/api/login", data=json.dumps({"token": token}).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as res:
        cookie = (res.headers.get("Set-Cookie") or "").split(";")[0]
    request = urllib.request.Request(f"{base}/api/app/quit", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json", "Cookie": cookie})
    urllib.request.urlopen(request, timeout=10).close()
    for _ in range(200):  # servers get 120s each to save, in parallel
        if not dashboard.already_running(PORT):
            return 0
        time.sleep(1)
    return 1


def tray(open_dashboard, quit_app, first_run: bool) -> None:
    """The tray icon. Blocks until Quit. Without pystray (running from
    source), it just waits."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        threading.Event().wait()
        return
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((2, 2, 62, 62), fill=(200, 164, 92, 255))
    draw.polygon([(16, 16), (26, 16), (32, 38), (38, 16), (48, 16), (36, 50), (28, 50)], fill=(20, 17, 14, 255))

    def on_quit(icon, _item):
        icon.notify("Stopping your servers and saving their worlds...", "Valheim Deploy")
        quit_app()
        icon.stop()

    icon = pystray.Icon("ValheimDeploy", image, "Valheim Deploy", menu=pystray.Menu(
        pystray.MenuItem("Open dashboard", lambda *_: open_dashboard(), default=True),
        pystray.MenuItem("Quit (stops servers safely)", on_quit)))

    def ready(icon):
        icon.visible = True
        if first_run:
            icon.notify("Valheim Deploy keeps running here. Closing the window does not stop your "
                        "servers; use Quit in this menu to stop them.", "Valheim Deploy")

    icon.run(setup=ready)


def run(no_window: bool) -> int:
    from . import cli, dashboard, registry, winhost
    from .winhost import network
    from .winhost.manager import LocalAgent, ServerManager

    if dashboard.already_running(PORT):
        # Double-clicked again: the window is what they want.
        if not no_window:
            open_window(login_url())
        return 0

    cli.HOST_CHOICES[:] = ["windows"]
    cli.ADVANCED_NOTICE = cli.HOME_NOTICE
    manager = ServerManager()
    winhost.set_manager(manager)
    manager.load()

    token = dashboard.dashboard_token()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), dashboard.make_handler(token, PORT, LocalAgent(manager)))
    server.daemon_threads = True
    stopping, finished = threading.Event(), threading.Event()

    def quit_app() -> None:
        if stopping.is_set():
            finished.wait()
            return
        stopping.set()
        manager.stop_all()  # every world saved before anything exits
        server.shutdown()
        finished.set()

    dashboard.QUIT = quit_app
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=manager.autostart, daemon=True).start()
    threading.Thread(target=network.refresh_mappings, args=(registry.all_servers(),), daemon=True).start()

    if not no_window:
        open_window(login_url())
    marker = registry.home() / "tray-explained"
    first_run = not marker.exists()
    marker.touch()
    if no_window:
        finished.wait()  # until Quit through the API
    else:
        # A Quit from the page or `--quit` must also take the tray icon down.
        threading.Thread(target=lambda: (finished.wait(), os._exit(0)), daemon=True).start()
        tray(lambda: open_window(login_url()), quit_app, first_run)
        quit_app()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--send-ctrl-c"]:
        from .winhost import ctrlc
        return ctrlc.send(int(argv[1]))
    if argv[:1] == ["--firewall"]:
        from .winhost import network
        return network.apply_firewall(argv[1], argv[2], int(argv[3]))
    if argv[:1] == ["--version"]:
        from . import __version__
        print(__version__)
        return 0
    if argv[:1] == ["--quit"]:
        return quit_running()
    return run(no_window="--no-window" in argv)


if __name__ == "__main__":
    raise SystemExit(main())
