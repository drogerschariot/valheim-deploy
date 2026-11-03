"""Every server this PC runs, and the in-process stand-in for host/agent.py."""

from __future__ import annotations

import ctypes
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from .. import registry
from . import IS_WINDOWS, PROVIDER, instance_config, steam
from .backend import WindowsBackend
from .collect import WinCollectors

import agent as host_agent  # noqa: E402

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


class ServerManager:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.servers: dict[str, tuple[WindowsBackend, WinCollectors]] = {}
        self.updating = False
        self._awake = False

    # -- the set of servers ----------------------------------------------

    def load(self) -> None:
        for rec in registry.all_servers():
            if rec["provider"] == PROVIDER and rec.get("status") == "installed":
                self.ensure(rec)
        threading.Thread(target=self._keep_awake, daemon=True).start()

    def ensure(self, rec: dict[str, Any]) -> tuple[WindowsBackend, WinCollectors]:
        with self.lock:
            if rec["name"] not in self.servers:
                backend = WindowsBackend(instance_config(rec), rec["secrets"]["password"])
                self.servers[rec["name"]] = (backend, WinCollectors(backend.cfg, backend))
            return self.servers[rec["name"]]

    def get(self, name: str) -> WindowsBackend | None:
        entry = self.servers.get(name)
        return entry[0] if entry else None

    def autostart(self) -> list[str]:
        started = []
        for rec in registry.all_servers():
            if rec["provider"] == PROVIDER and rec.get("status") == "installed" \
                    and rec["settings"].get("autostart", True):
                self.ensure(rec)[0].control("start")
                started.append(rec["name"])
        return started

    def remove(self, name: str) -> None:
        with self.lock:
            entry = self.servers.pop(name, None)
        if entry:
            entry[0].close()

    def stop_all(self) -> None:
        """Stop every server at once, each saving its world. Used on Quit."""
        threads = [threading.Thread(target=b.close) for b, _ in list(self.servers.values())]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def update_game(self, ui: Any) -> None:
        """Stop what is running, back it up, update from Steam, start it again."""
        with self.lock:
            if self.updating:
                raise SystemExit("An update is already running.")
            self.updating = True
        try:
            running = [name for name, (b, _) in self.servers.items() if b.running()]
            for name in running:
                backend = self.servers[name][0]
                ui.say(f"Stopping {name} so it saves...")
                backend.control("stop")
                ok, detail = backend.backup()
                ui.say(f"  {detail}")
            steam.install_game(ui)
            for name in running:
                ui.say(f"Starting {name}...")
                self.servers[name][0].control("start")
        finally:
            self.updating = False

    def _keep_awake(self) -> None:
        """Stop Windows sleeping while anyone could be playing."""
        while IS_WINDOWS:
            want = any(b.running() for b, _ in list(self.servers.values()))
            if want != self._awake:
                flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if want else 0)
                ctypes.windll.kernel32.SetThreadExecutionState(flags)
                self._awake = want
            time.sleep(30)


class LocalAgent:
    """Answers the dashboard the way host/agent.py does over HTTP, but by
    calling the manager directly. Same paths, same JSON."""

    def __init__(self, manager: ServerManager) -> None:
        self.manager = manager

    def call(self, rec: dict[str, Any], path: str, body: dict | None = None,
             timeout: float = 200) -> tuple[int, Any]:
        backend, collectors = self.manager.ensure(rec)
        route = urlparse(path)
        method = "GET" if body is None else "POST"
        status, payload = host_agent.handle(collectors, backend, method, route.path,
                                            parse_qs(route.query), body or {})
        return int(status), payload
