"""Where a server can live. Every provider answers the same few questions; only
creating the machine differs. After that, all Linux targets share one SSH
install, so a server behaves the same wherever it runs.
"""

from __future__ import annotations

import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path
from typing import Any

from .. import paths, registry, settings, ssh


def sizing(players: int) -> int:
    """GB of RAM to recommend. Memory tracks how explored the world is more
    than how many are playing, so this errs towards room to grow."""
    if players <= 4:
        return 4
    return 8 if players <= 10 else 16


def my_public_ip() -> str:
    with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=10) as res:
        return res.read().decode().strip()


def show_steps(ui: Any, title: str, steps: list[dict[str, str]]) -> None:
    ui.heading(title)
    for i, step in enumerate(steps, 1):
        ui.say(f"  {i}. {step['text']}")
        if step.get("link"):
            ui.say(f"     {step['link']}")


def extract_tar_stream(stream: Any, dest: Path) -> int:
    """Unpack a tar stream. In Python rather than a `tar` command so it behaves
    the same everywhere, and with the data filter because these bytes came from
    a remote machine and must not be able to write outside dest."""
    try:
        with tarfile.open(fileobj=stream, mode="r|") as tar:
            for member in tar:
                if member.isfile():
                    tar.extract(member, dest, filter="data")
        return 0
    except (tarfile.TarError, OSError):
        return 1


class Provider:
    key = ""
    label = ""
    needs_network_setup = False  # True: the user must arrange port forwarding
    # For hosts that need an account: how to make one and how to get the
    # credentials this app needs, shared by the terminal and the browser.
    # {"service", "signup": [step], "credentials": [step], "fields": [field], "stored"}
    guide: dict[str, Any] | None = None

    def connected(self) -> str | None:
        """Which account the saved credentials reach, or None if there are
        none or they no longer work."""
        return None

    def connect(self, fields: dict[str, str]) -> str:
        """Check credentials with the provider and keep them only if they work."""
        raise NotImplementedError

    def walk_through(self, ui: Any) -> str:
        """Use the saved account, or guide the user to one in the terminal."""
        account = self.connected()
        if account:
            return account
        g = self.guide
        if ui.scripted:
            values = {f["key"]: ui.answers.get(f["key"]) for f in g["fields"]}
            if not all(values.values()):
                raise SystemExit(f"Connect your {g['service']} account first.")
            return self.connect(values)
        if not ui.confirm(f"has_{self.key}_account", f"Do you already have an account with {g['service']}?", False):
            show_steps(ui, f"Create your {g['service']} account", g["signup"])
            ui.ask("account_ready", "Press Enter when your account is ready", "ready")
        show_steps(ui, f"Get what this app needs from {g['service']}", g["credentials"])
        ui.say(g["stored"])
        while True:
            values = {f["key"]: (ui.secret(f["key"], f["label"]) if f["secret"]
                                 else ui.ask(f["key"], f["label"])) for f in g["fields"]}
            try:
                account = self.connect(values)
            except SystemExit as exc:
                ui.warn(str(exc.code))
                continue
            ui.ok(f"Connected: {account}.")
            return account

    def preflight(self) -> str | None:
        """What is missing on this computer, or None if ready."""
        for tool in ("ssh", "ssh-keygen"):
            if not shutil.which(tool):
                return (f"{tool} was not found. On Windows, add the OpenSSH Client under "
                        "Settings > System > Optional features.")
        return None

    def warnings(self) -> list[str]:
        return []

    def wizard_options(self, params: dict[str, str]) -> dict[str, Any]:
        """Choices for the browser wizard."""
        return {}

    def collect(self, ui: Any, rec: dict[str, Any]) -> None:
        """Ask provider-specific questions. Changes nothing outside the record."""

    def describe_cost(self, rec: dict[str, Any]) -> str:
        return "No hosting bill from this tool."

    def create(self, ui: Any, rec: dict[str, Any]) -> None:
        """Bring up the machine and fill in rec['address'] and rec['ssh']."""

    def install(self, ui: Any, rec: dict[str, Any]) -> None:
        ssh.wait_until_up(rec)
        ui.say("Installing on the host. This streams the installer's own output.")
        config = settings.host_config(rec["name"], rec["settings"], rec["ssh"].get("port", 22))
        if ssh.install(rec, config, rec["secrets"]) != 0:
            raise SystemExit("The installer failed; its output is above. Fix the cause, then "
                             f"retry with `{paths.command()} apply {rec['name']}`.")

    def update(self, ui: Any, rec: dict[str, Any]) -> int:
        cmd = f"{ssh.sudo(rec)}/opt/valheim/bin/valheim-update.sh {ssh.quote(rec['name'])}"
        return ssh.run(rec, cmd).returncode

    def fetch_backups(self, ui: Any, rec: dict[str, Any]) -> int:
        """Copy every archive to ~/.valheim-deploy/backups/<name>/."""
        dest = registry.home() / "backups" / rec["name"]
        dest.mkdir(parents=True, exist_ok=True)
        remote = f"/opt/valheim/instances/{rec['name']}/backups"
        command = f"{ssh.sudo(rec)}tar -C {ssh.quote(remote)} -cf - ."
        with subprocess.Popen(["ssh", *ssh.options(rec), ssh.target(rec), command],
                              stdout=subprocess.PIPE) as src:
            return extract_tar_stream(src.stdout, dest) or src.wait()

    def allow_ip(self, ui: Any, rec: dict[str, Any], ip: str) -> None:
        raise SystemExit(f"{self.label} has no cloud firewall managed by this tool.")

    def destroy(self, ui: Any, rec: dict[str, Any]) -> None:
        raise NotImplementedError

    def network_notes(self, rec: dict[str, Any]) -> list[str]:
        return []


def get(key: str) -> Provider:
    from .aws import AWS
    from .existing import Existing
    from .hetzner import Hetzner
    from .local import Local
    return {p.key: p for p in (Local(), Existing(), AWS(), Hetzner())}[key]
