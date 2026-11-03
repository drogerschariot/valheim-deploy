"""A Linux machine the user already has: a home server, a Proxmox VM, or a VPS
from any provider. All this needs is SSH."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from .. import ssh
from . import Provider, show_steps

VPS_STEPS = [
    {"text": "Rent a virtual server (VPS) from any provider, for example DigitalOcean, Akamai Linode, "
             "Vultr or OVHcloud. Hetzner and AWS have their own option, which does every step for you."},
    {"text": "Choose Ubuntu 24.04 on x86-64 (also called AMD64 or Intel, not ARM), with at least 4 GB of RAM."},
    {"text": "When it asks for an SSH key, paste the public key from \"Make a key\" below. The login "
             "user is usually root."},
    {"text": "If the provider has a firewall, allow UDP 2456-2457 in from anywhere. Those are the game "
             "ports; the summary at the end confirms them."},
    {"text": "When it is running, continue and enter its public IP address."},
]


def make_key() -> dict[str, str]:
    """A fresh key pair for a server the user is about to rent."""
    path = ssh.generate_key(f"byo-{secrets.token_hex(3)}")
    return {"path": path, "public_key": Path(f"{path}.pub").read_text().strip()}


def login_problem(rec: dict[str, Any]) -> str | None:
    if not ssh.reachable(rec):
        return f"Could not log in to {ssh.target(rec)}. Check the address, user and key."
    if rec["ssh"]["user"] != "root" and ssh.run(rec, "sudo -n true", capture=True).returncode != 0:
        return "That user cannot run sudo without a password, which the installer needs."
    return None


class Existing(Provider):
    key = "existing"
    label = "A Linux server I already have (Ubuntu, over SSH)"
    needs_network_setup = True

    def wizard_options(self, params: dict[str, str]) -> dict[str, Any]:
        return {"vps_steps": VPS_STEPS}

    def collect(self, ui: Any, rec: dict[str, Any]) -> None:
        made = None
        if not ui.scripted and not ui.confirm("has_server", "Do you already have a Linux server for this?", True):
            show_steps(ui, "Rent a server", VPS_STEPS)
            made = make_key()
            ui.say(f"\nPaste this public key when the provider asks for an SSH key:\n\n{made['public_key']}\n")
            ui.ask("server_ready", "Press Enter when the server is running", "ready")
        ui.say("Needs: x86-64 Ubuntu 20.04+, at least 4 GB of RAM and 10 GB of free disk, and an SSH")
        ui.say("login that is root or has passwordless sudo. Set that up before continuing.")
        host = ui.ask("ssh_host", "SSH address (IP or hostname)")
        user = ui.ask("ssh_user", "SSH user", "root")
        port = int(ui.ask("ssh_port", "SSH port", "22"))
        key = ui.ask("ssh_key", "Path to the SSH private key (- uses your ssh-agent/default key)",
                     made["path"] if made else "-")
        rec["ssh"] = {"host": host, "user": user, "port": port, "key": "" if key == "-" else key}
        rec["address"] = ui.ask("public_address", "Public IP or DNS name players will join", host)
        ui.say("\nThe installer can turn on ufw with default-deny inbound, opening only SSH and the")
        ui.say("game ports. On a box that runs anything else, this blocks those services. If you")
        ui.say("say no, an already-active ufw still gets the game ports opened.")
        rec["settings"]["firewall_enabled"] = ui.confirm("manage_firewall", "Let the installer manage ufw?", False)

        ui.say("Testing the SSH login...")
        problem = login_problem(rec)
        if problem:
            raise SystemExit(problem)
        ui.ok("SSH login works.")

    def destroy(self, ui: Any, rec: dict[str, Any]) -> None:
        code = ssh.push_and_run(rec, {}, f"--uninstall {ssh.quote(rec['name'])} --purge")
        if code != 0:
            raise SystemExit("The uninstall failed; the record has been kept so you can retry.")

    def network_notes(self, rec: dict[str, Any]) -> list[str]:
        port = rec["settings"]["port"]
        return [
            f"If this machine is behind a router, forward UDP {port}-{port + 1} to it.",
            f"If its provider has a cloud firewall, allow UDP {port}-{port + 1} inbound there.",
            "Do not open any TCP port for the dashboard: it reaches the server over SSH.",
        ]
