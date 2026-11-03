"""This Windows PC, running Valheim's own Windows dedicated server. Used by
the Windows app, which runs the servers itself (see vhdeploy/winhost)."""

from __future__ import annotations

import shutil
import time
from typing import Any

from .. import registry
from . import Provider


def write_admins(path: Any, admins: list[str]) -> None:
    """Merge rather than replace, so admins added on the dashboard survive."""
    have = [l.strip() for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    merged = have + [a for a in admins if a not in have]
    path.write_text("\n".join(merged) + ("\n" if merged else ""), encoding="utf-8")


class WindowsPC(Provider):
    key = "windows"
    label = "This Windows PC"
    needs_network_setup = True
    waits_itself = True

    def preflight(self) -> str | None:
        return None  # no SSH or Docker needed

    def warnings(self) -> list[str]:
        return ["The server only runs while this PC is on. The app keeps it from going to sleep "
                "while a server is running."]

    def create(self, ui: Any, rec: dict[str, Any]) -> None:
        from ..winhost import network
        rec["address"] = network.public_ip() or "your-public-ip"

    def install(self, ui: Any, rec: dict[str, Any]) -> None:
        from .. import winhost
        from ..winhost import steam
        from ..winhost.backend import WindowsBackend
        manager = winhost.get_manager()
        if not WindowsBackend.installed_game():
            steam.install_game(ui)
        rec["status"] = "installed"
        registry.save(rec)
        backend = manager.ensure(rec)[0]
        write_admins(backend.data_dir / "adminlist.txt", rec["settings"]["admins"])
        ui.say("Starting the server. The first start builds the world, which takes a minute or two...")
        backend.control("start")
        deadline = time.time() + rec["settings"]["start_timeout"]
        while time.time() < deadline:
            if backend.ready:
                ui.ok("The server is up.")
                return
            if not backend.running() and backend.phase == "not installed":
                raise SystemExit("The game did not install. Try Update game.")
            time.sleep(2)
        ui.warn("The server has not said it is ready yet. It may still be loading; see its Logs tab.")

    def update(self, ui: Any, rec: dict[str, Any]) -> int:
        from .. import winhost
        winhost.get_manager().update_game(ui)
        return 0

    def fetch_backups(self, ui: Any, rec: dict[str, Any]) -> int:
        from .. import winhost
        source = winhost.instance_dir(rec["name"]) / "backups"
        dest = registry.home() / "backups" / rec["name"]
        dest.mkdir(parents=True, exist_ok=True)
        for path in source.glob("*.zip"):
            shutil.copy2(path, dest / path.name)
        return 0

    def destroy(self, ui: Any, rec: dict[str, Any]) -> None:
        from .. import winhost
        from ..winhost import network
        ui.say("Stopping the server so it saves...")
        winhost.get_manager().remove(rec["name"])
        network.close_ports(rec)
        if network.firewall_rule_exists(rec["name"]):
            ui.say("Removing its firewall rule. Windows asks for permission.")
            network.elevate_firewall("remove", rec["name"], rec["settings"]["port"])
        shutil.rmtree(winhost.instance_dir(rec["name"]), ignore_errors=True)

    def describe_cost(self, rec: dict[str, Any]) -> str:
        return "No hosting bill: it runs on this PC."

    def network_notes(self, rec: dict[str, Any]) -> list[str]:
        port = rec["settings"]["port"]
        return [f"Open the Network tab: it checks your firewall, router and public IP, and can "
                f"forward UDP {port}-{port + 1} for you."]
