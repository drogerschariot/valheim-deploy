"""SSH, used for every remote host: installing, and tunnelling to the agent.

The tunnel is why the agent never needs a port open. The owner already has key
access to the box, so the dashboard reuses it.
"""

from __future__ import annotations

import io
import json
import os
import shlex
import socket
import subprocess
import tarfile
import threading
import time
from pathlib import Path
from typing import Any

from . import output, paths, registry

HOST_DIR = paths.host_dir()
HOST_FILES = ["a2s.py", "agent.py", "launcher.py", "install.py", "valheim-backup.sh", "valheim-update.sh"]


def generate_key(name: str) -> str:
    key = registry.home() / "keys" / name
    if not key.exists():
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"valheim-{name}", "-f", str(key)],
                       check=True)
        if os.name == "nt":
            # Windows OpenSSH refuses a key that inherits broad permissions.
            subprocess.run(["icacls", str(key), "/inheritance:r", "/grant:r",
                            f"{os.environ.get('USERNAME', '')}:F"],
                           check=True, stdout=subprocess.DEVNULL)
    return str(key)


def options(rec: dict[str, Any]) -> list[str]:
    s = rec["ssh"]
    # A known_hosts per server: first contact is trusted, any later change of
    # host key fails, and the user's own known_hosts is left alone.
    known = registry.home() / "keys" / f"{rec['name']}.known_hosts"
    opts = ["-p", str(s.get("port", 22)), "-o", f'UserKnownHostsFile="{known.as_posix()}"',
            "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR"]
    if s.get("key"):
        opts += ["-i", s["key"], "-o", "IdentitiesOnly=yes"]
    return opts


def target(rec: dict[str, Any]) -> str:
    return f"{rec['ssh']['user']}@{rec['ssh']['host']}"


def sudo(rec: dict[str, Any]) -> str:
    return "" if rec["ssh"]["user"] == "root" else "sudo -n "


def run(rec: dict[str, Any], command: str, capture: bool = False,
        timeout: float | None = None, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    argv = ["ssh", *options(rec), target(rec), command]
    if not capture:
        return subprocess.CompletedProcess(argv, output.stream(argv, stdin))
    return subprocess.run(argv, timeout=timeout, check=False, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True)


def reachable(rec: dict[str, Any]) -> bool:
    try:
        return run(rec, "true", capture=True, timeout=25).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def wait_until_up(rec: dict[str, Any], minutes: int = 5) -> None:
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        if reachable(rec):
            return
        time.sleep(5)
    raise SystemExit(f"Could not reach {rec['ssh']['host']} over SSH within {minutes} minutes.")


def bundle(extra: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name in HOST_FILES:
            tar.add(HOST_DIR / name, arcname=name)
        for name, content in extra.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o600
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def push_and_run(rec: dict[str, Any], files: dict[str, str], command: str) -> int:
    """Unpack the host bundle in a private temp directory, run the installer,
    and always clean up. Secrets travel in the tarball, never in an argv that
    `ps` could show."""
    script = (
        'set -e; d=$(mktemp -d); trap \'rm -rf "$d"\' EXIT; tar xz -C "$d"; '
        f'{sudo(rec)}python3 "$d/install.py" {command}'
    )
    return run(rec, script, stdin=bundle(files)).returncode


def install(rec: dict[str, Any], config: dict[str, Any], secrets: dict[str, str]) -> int:
    return push_and_run(
        rec, {"config.json": json.dumps(config), "secrets.json": json.dumps(secrets)},
        '--config "$d/config.json" --secrets "$d/secrets.json" --ssh-port "${SSH_CONNECTION##* }"',
    )


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Tunnels:
    """One lazily opened `ssh -N -L` per server, reopened if it dies. A server
    that just failed is left alone briefly so a dead host does not stall every
    poll by a connect timeout."""

    COOLDOWN = 15

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.open: dict[str, tuple[subprocess.Popen, int]] = {}
        self.failed: dict[str, tuple[float, str]] = {}

    def port_for(self, rec: dict[str, Any]) -> int:
        name = rec["name"]
        with self.lock:
            current = self.open.get(name)
            if current and current[0].poll() is None:
                return current[1]
            when, why = self.failed.get(name, (0.0, ""))
            if time.time() - when < self.COOLDOWN:
                raise ConnectionError(why)
            local = free_port()
            remote = rec["settings"]["port"] + 16000
            proc = subprocess.Popen(
                ["ssh", *options(rec), "-N", "-o", "ExitOnForwardFailure=yes",
                 "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                 "-L", f"127.0.0.1:{local}:127.0.0.1:{remote}", target(rec)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            )
            deadline = time.time() + 15
            while time.time() < deadline and proc.poll() is None:
                try:
                    socket.create_connection(("127.0.0.1", local), 0.3).close()
                    self.open[name] = (proc, local)
                    self.failed.pop(name, None)
                    return local
                except OSError:
                    time.sleep(0.2)
            proc.kill()
            error = (proc.communicate()[1] or "").strip() or "timed out"
            why = f"SSH tunnel failed: {error}"
            self.failed[name] = (time.time(), why)
            raise ConnectionError(why)

    def drop(self, name: str) -> None:
        with self.lock:
            current = self.open.pop(name, None)
        if current:
            current[0].terminate()

    def close(self) -> None:
        for name in list(self.open):
            self.drop(name)


def quote(value: str) -> str:
    return shlex.quote(value)
