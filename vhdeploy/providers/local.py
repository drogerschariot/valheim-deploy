"""This computer, through Docker. The only target that works on macOS and
Windows, since the server is a Linux binary."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .. import output, registry, settings, ssh
from . import Provider, my_public_ip

IMAGE = "valheim-deploy:local"


def docker(*args: str, capture: bool = False) -> subprocess.CompletedProcess:
    argv = ["docker", *args]
    if not capture:
        return subprocess.CompletedProcess(argv, output.stream(argv))
    return subprocess.run(argv, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def container(rec: dict[str, Any]) -> str:
    return f"valheim-{rec['name']}"


def is_arm() -> bool:
    return platform.machine().lower() in ("arm64", "aarch64")


class Local(Provider):
    key = "local"
    label = "This computer (Docker)"
    needs_network_setup = True

    def preflight(self) -> str | None:
        if not shutil.which("docker") or docker("info", capture=True).returncode != 0:
            return ("Docker is not installed or not running. Install Docker Desktop "
                    "(https://www.docker.com/products/docker-desktop/), start it, and try again.")
        return None

    def warnings(self) -> list[str]:
        notes = ["The server stops whenever this computer sleeps or shuts down."]
        if is_arm():
            notes.insert(0, "This is an ARM computer (such as Apple Silicon) and Valheim's server is x86-64 "
                            "only, so it would run under emulation: expect slow loading, possible crashes, "
                            "and poor performance with several players. A cloud or Linux host is strongly "
                            "recommended.")
        return notes

    def collect(self, ui: Any, rec: dict[str, Any]) -> None:
        for note in self.warnings():
            ui.warn(note)
        if is_arm():
            if not ui.confirm("arm_ok", "Continue anyway?", False):
                raise SystemExit("Stopped. Start again and pick another host.")

    def create(self, ui: Any, rec: dict[str, Any]) -> None:
        try:
            rec["address"] = my_public_ip()
        except OSError:
            rec["address"] = "your-public-ip"

    def _env_file(self, rec: dict[str, Any]) -> Path:
        path = registry.home() / "keys" / f"{rec['name']}.docker.env"
        config = settings.host_config(rec["name"], rec["settings"])
        # docker --env-file takes values literally, to the end of the line, and
        # keeps the secrets out of the docker command's own argv.
        registry.write_private(path, "".join(
            f"{k}={v}\n" for k, v in {
                "VALHEIM_SERVER_PASSWORD": rec["secrets"]["password"],
                "VALHEIM_AGENT_TOKEN": rec["secrets"]["agent_token"],
                "VALHEIM_CONFIG_JSON": json.dumps(config),
            }.items()))
        return path

    def install(self, ui: Any, rec: dict[str, Any]) -> None:
        ui.say("Building the server image (first time only takes a minute or two).")
        built = docker("build", "--platform", "linux/amd64", "-t", IMAGE, str(ssh.HOST_DIR))
        if built.returncode != 0:
            raise SystemExit("The image failed to build; the output is above.")
        name, port = container(rec), rec["settings"]["port"]
        if docker("inspect", name, capture=True).returncode == 0:
            ui.say("Replacing the container. The world is on a volume and is kept.")
            docker("stop", "-t", "130", name, capture=True)
            docker("rm", name, capture=True)
        run = docker(
            "run", "-d", "--name", name, "--platform", "linux/amd64", "--restart", "unless-stopped",
            "--stop-timeout", "130",  # the world write budget, plus margin
            "--env-file", str(self._env_file(rec)),
            "-v", f"{name}:{settings.host_config(rec['name'], rec['settings'])['instance_dir']}",
            "-v", f"{name}-steam:/opt/valheim/steamcmd",
            "-p", f"{port}:{port}/udp", "-p", f"{port + 1}:{port + 1}/udp",
            # Loopback only: the agent can stop the server.
            "-p", f"127.0.0.1:{port + settings.AGENT_PORT_OFFSET}:8080",
            IMAGE,
        )
        if run.returncode != 0:
            raise SystemExit("The container failed to start; the output is above.")

    def update(self, ui: Any, rec: dict[str, Any]) -> int:
        ui.say("Restarting: the container backs up and updates from Steam each time it starts.")
        return docker("restart", "-t", "130", container(rec)).returncode

    def fetch_backups(self, ui: Any, rec: dict[str, Any]) -> int:
        dest = registry.home() / "backups" / rec["name"]
        dest.mkdir(parents=True, exist_ok=True)
        remote = f"/opt/valheim/instances/{rec['name']}/backups/."
        return docker("cp", f"{container(rec)}:{remote}", str(dest), capture=True).returncode

    def destroy(self, ui: Any, rec: dict[str, Any]) -> None:
        name = container(rec)
        docker("stop", "-t", "130", name, capture=True)
        docker("rm", name, capture=True)
        docker("volume", "rm", name, f"{name}-steam", capture=True)
        (registry.home() / "keys" / f"{rec['name']}.docker.env").unlink(missing_ok=True)

    def network_notes(self, rec: dict[str, Any]) -> list[str]:
        port = rec["settings"]["port"]
        return [
            f"Forward UDP {port}-{port + 1} on your router to this computer's LAN address.",
            "Give this computer a fixed LAN address (a DHCP reservation), or the forward breaks.",
            f"Allow UDP {port}-{port + 1} through this computer's own firewall.",
            "Find your public IP (https://checkip.amazonaws.com). It may change, and a server "
            "behind CGNAT cannot be reached this way at all.",
        ]
