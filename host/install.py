#!/usr/bin/env python3
"""Installs or removes one Valheim instance on an x86-64 Ubuntu host.

Idempotent: re-running with new settings is how a change is applied. Pushed
over SSH by the CLI and run as root. Standard library only.

  install.py --config config.json --secrets secrets.json
  install.py --uninstall NAME [--purge]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/opt/valheim")
STEAM_DIR = ROOT / "steamcmd"
ETC = Path("/etc/valheim")
STEAM_APP_ID = "896660"
STEAMCMD_URL = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz"
PACKAGES = ["ca-certificates", "curl", "libatomic1", "libpulse-dev", "libpulse0",
            "lib32gcc-s1", "lib32stdc++6", "python3", "tar", "ufw"]
HERE = Path(__file__).resolve().parent


def say(message: str) -> None:
    print(f"==> {message}", flush=True)


def sh(*argv: str, check: bool = True, quiet: bool = False, env: dict | None = None) -> subprocess.CompletedProcess:
    kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "text": True} if quiet else {}
    return subprocess.run(argv, check=check, env=env, **kwargs)


def preflight() -> None:
    if os.geteuid() != 0:
        sys.exit("The installer must run as root.")
    if platform.machine() != "x86_64":
        sys.exit(f"Valheim's dedicated server is x86-64 only; this host is {platform.machine()}.")
    release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    if release.get("ID", "").strip('"') != "ubuntu":
        sys.exit("Only Ubuntu hosts are supported.")
    # The server links against GLIBC 2.29, so an older base image fails at exec
    # time with a link error rather than anything self-explanatory.
    version = release.get("VERSION_ID", "0").strip('"')
    if tuple(int(x) for x in version.split(".")) < (20, 4):
        sys.exit(f"Valheim needs Ubuntu 20.04 or later; this host reports {version}.")


def install_packages() -> None:
    # A fresh cloud VM is still running cloud-init, which holds the apt lock.
    if shutil.which("cloud-init"):
        say("Waiting for first-boot setup to finish")
        sh("cloud-init", "status", "--wait", check=False, quiet=True)
    say("Installing system packages")
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
    apt = ["apt-get", "-o", "DPkg::Lock::Timeout=300"]
    sh(*apt, "update", "-qq", env=env)
    sh(*apt, "install", "-y", "-qq", "--no-install-recommends", *PACKAGES, env=env)


def ensure_accounts() -> None:
    # Separate primary groups, so the game cannot read the agent's token.
    for user, extra in (("valheim", []), ("valheim-agent", ["--groups", "valheim,systemd-journal"])):
        sh("groupadd", "--system", "--force", user)
        if sh("id", user, check=False, quiet=True).returncode != 0:
            # Neither needs a shell: every path in goes through systemd or sudo.
            sh("useradd", "--system", "--no-create-home", "--home-dir", str(ROOT),
               "--shell", "/usr/sbin/nologin", "--gid", user, *extra, user)
        else:
            sh("usermod", "--gid", user, user)


def install_files() -> None:
    for path in (ROOT, ROOT / "app", ROOT / "bin", ROOT / "instances", ETC):
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o755)
    # Named in the agent unit's ReadWritePaths, and systemd refuses to start a
    # unit whose path is missing, so this must exist whatever else happens.
    STEAM_DIR.mkdir(exist_ok=True)
    shutil.chown(STEAM_DIR, "valheim", "valheim")
    for name in ("a2s.py", "agent.py", "launcher.py"):
        shutil.copy(HERE / name, ROOT / "app" / name)
    for name in ("valheim-backup.sh", "valheim-update.sh"):
        shutil.copy(HERE / name, ROOT / "bin" / name)
        (ROOT / "bin" / name).chmod(0o755)


def steam_cmd(*args: str) -> list[str]:
    return ["runuser", "-u", "valheim", "--", "env", f"HOME={STEAM_DIR}",
            str(STEAM_DIR / "steamcmd.sh"), *args]


def install_game(instance_dir: Path) -> None:
    server_dir = instance_dir / "server"
    if (server_dir / "valheim_server.x86_64").exists():
        return  # Updating a live server is a deliberate act: `valheim update`.
    if os.environ.get("VALHEIM_SKIP_STEAM"):
        return
    if not (STEAM_DIR / "steamcmd.sh").exists():
        say("Downloading SteamCMD")
        sh("bash", "-c", f"curl -fsSL {shlex.quote(STEAMCMD_URL)} | tar -xz -C {shlex.quote(str(STEAM_DIR))}")
        sh("chown", "-R", "valheim:valheim", str(STEAM_DIR))
    say("Downloading the Valheim server (about 2 GB, usually a few minutes)")
    # steamcmd commonly exits non-zero on its first, self-updating run.
    for attempt in range(1, 4):
        done = sh(*steam_cmd("+force_install_dir", str(server_dir), "+login", "anonymous",
                             "+app_update", STEAM_APP_ID, "validate", "+quit"), check=False)
        if done.returncode == 0 and (server_dir / "valheim_server.x86_64").exists():
            return
        say(f"SteamCMD attempt {attempt} did not finish; retrying")
        time.sleep(15)
    sys.exit("SteamCMD could not install the Valheim server.")


SERVER_UNIT = """\
[Unit]
Description=Valheim dedicated server ({name})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=valheim
Group=valheim
WorkingDirectory={dir}/server
EnvironmentFile=/etc/valheim/{name}.env
ExecStart=/usr/bin/python3 /opt/valheim/app/launcher.py {name}

# The two lines that protect the world. Valheim writes its save only on SIGINT;
# systemd's default SIGTERM is ignored outright, so a stock unit would wait out
# the timeout and SIGKILL a process that saved nothing since its last autosave.
# 120s is the write budget: being killed mid-write is worse than waiting.
KillSignal=SIGINT
KillMode=mixed
TimeoutStopSec=120

Restart=on-failure
RestartSec=20

NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectHome=true
ProtectSystem=strict
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
ReadWritePaths={dir}

[Install]
WantedBy=multi-user.target
"""

AGENT_UNIT = """\
[Unit]
Description=Valheim agent ({name})
After=network-online.target valheim-server-{name}.service
Wants=network-online.target

[Service]
Type=simple
User=valheim-agent
Group=valheim-agent
SupplementaryGroups=systemd-journal valheim
EnvironmentFile=/etc/valheim/{name}.agent.env
ExecStart=/usr/bin/python3 /opt/valheim/app/agent.py {name}
Restart=on-failure
RestartSec=5

# NoNewPrivileges and RestrictSUIDSGID stay off because the control surface
# runs through sudo, which gains privilege by design. Backup and update-check
# run via sudo and so inherit this unit's mount namespace, not their own: the
# paths they write must be listed here.
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ReadWritePaths={dir}/data {dir}/backups /opt/valheim/steamcmd

[Install]
WantedBy=multi-user.target
"""

BACKUP_UNIT = """\
[Unit]
Description=Back up the Valheim world ({name})

[Service]
Type=oneshot
User=valheim
Group=valheim
ExecStart=/opt/valheim/bin/valheim-backup.sh {name}
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={dir}/backups
"""

BACKUP_TIMER = """\
[Unit]
Description=Daily Valheim world backup ({name})

[Timer]
OnCalendar=*-*-* {time}:00
RandomizedDelaySec=900
Persistent=true

[Install]
WantedBy=timers.target
"""

SUDOERS = """\
# The agent's entire privileged surface. Every command is fully qualified.
# Note what is absent: no shell, no unit editing, and no valheim-update.sh
# without --check -- applying an update is `valheim update`, run by the owner.
valheim-agent ALL=(root) NOPASSWD: /usr/bin/systemctl start {server}
valheim-agent ALL=(root) NOPASSWD: /usr/bin/systemctl stop {server}
valheim-agent ALL=(root) NOPASSWD: /usr/bin/systemctl restart {server}
valheim-agent ALL=(root) NOPASSWD: /opt/valheim/bin/valheim-update.sh {name} --check
valheim-agent ALL=(valheim) NOPASSWD: /opt/valheim/bin/valheim-backup.sh {name}
"""


def write(path: Path, content: str, mode: int, group: str | None = None) -> bool:
    """Write only on a real change, and report it. Created with its final mode
    so a secret is never momentarily world-readable."""
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    path.chmod(mode)
    if group:
        shutil.chown(path, "root", group)
    return True


def env_value(value: str) -> str:
    # systemd EnvironmentFile: a double-quoted value with \ and " escaped.
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_instance(cfg: dict, secrets: dict) -> bool:
    name, instance_dir = cfg["name"], Path(cfg["instance_dir"])
    # data and backups are setgid: the agent rewrites the access lists from its
    # own account, and the game must still be able to read what it wrote.
    for sub, mode in (("", 0o750), ("server", 0o750), ("data", 0o2770), ("backups", 0o2770)):
        path = instance_dir / sub
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(mode)
        shutil.chown(path, "valheim", "valheim")

    changed = write(ETC / f"{name}.json", json.dumps(cfg, indent=2), 0o644)
    write(ETC / f"{name}.conf",
          f"INSTANCE_DIR={shlex.quote(str(instance_dir))}\n"
          f"WORLD_NAME={shlex.quote(cfg['world_name'])}\n"
          f"BACKUP_RETAIN={int(cfg['backup_retain'])}\n"
          f"START_TIMEOUT={int(cfg['start_timeout'])}\n", 0o644)
    # 0600 root: systemd reads it before dropping privileges, so neither the
    # agent nor anything else on the box needs to.
    changed |= write(ETC / f"{name}.env",
                     f"VALHEIM_SERVER_PASSWORD={env_value(secrets['password'])}\n", 0o600)
    write(ETC / f"{name}.agent.env",
          f"VALHEIM_AGENT_TOKEN={env_value(secrets['agent_token'])}\n", 0o640, "valheim-agent")

    units = Path("/etc/systemd/system")
    subs = {"name": name, "dir": instance_dir, "time": cfg["backup_time"]}
    changed |= write(units / f"valheim-server-{name}.service", SERVER_UNIT.format(**subs), 0o644)
    write(units / f"valheim-agent-{name}.service", AGENT_UNIT.format(**subs), 0o644)
    write(units / f"valheim-backup-{name}.service", BACKUP_UNIT.format(**subs), 0o644)
    write(units / f"valheim-backup-{name}.timer", BACKUP_TIMER.format(**subs), 0o644)

    sudoers = SUDOERS.format(name=name, server=f"valheim-server-{name}")
    candidate = ETC / f"{name}.sudoers.new"
    write(candidate, sudoers, 0o440)
    sh("visudo", "-cf", str(candidate), quiet=True)
    shutil.move(str(candidate), f"/etc/sudoers.d/valheim-{name}")

    data = instance_dir / "data"
    admin_file = data / "adminlist.txt"
    # Merged, not replaced: admins added from the dashboard live only in this
    # file, and re-applying settings must not quietly demote them.
    current = admin_file.read_text(encoding="utf-8").split() if admin_file.exists() else []
    admins = current + [a for a in cfg["admins"] if a not in current]
    admin_file.write_text("".join(f"{a}\n" for a in admins), encoding="utf-8")
    # Ban and permit lists belong to the running server from here on: created
    # when absent, never overwritten.
    for path in (admin_file, data / "bannedlist.txt", data / "permittedlist.txt"):
        path.touch(exist_ok=True)
        path.chmod(0o660)
        shutil.chown(path, "valheim", "valheim")
    return changed


def configure_firewall(cfg: dict) -> None:
    port = int(cfg["port"])
    rule = f"{port}:{port + 1}/udp"
    active = "Status: active" in sh("ufw", "status", quiet=True).stdout
    if cfg.get("firewall_enabled"):
        say("Configuring the host firewall (default deny; SSH and game ports open)")
        # SSH first, on the port sshd really received this session on, or
        # enabling the firewall locks the installer out mid-run. That can differ
        # from the port the CLI dialled when a router forwards 2222 to 22.
        sh("ufw", "allow", f"{int(cfg['ssh_port'])}/tcp", quiet=True)
        sh("ufw", "allow", rule, "comment", f"Valheim {cfg['name']}", quiet=True)
        sh("ufw", "default", "deny", "incoming", quiet=True)
        sh("ufw", "--force", "enable", quiet=True)
    elif active:
        # Declined to have the firewall managed, but one is on: without this
        # rule the server would be silently unreachable.
        sh("ufw", "allow", rule, "comment", f"Valheim {cfg['name']}", quiet=True)


def start(cfg: dict, restart: bool) -> None:
    name = cfg["name"]
    sh("systemctl", "daemon-reload")
    if cfg["backup_enabled"]:
        sh("systemctl", "enable", "--now", f"valheim-backup-{name}.timer", quiet=True)
        sh("systemctl", "restart", f"valheim-backup-{name}.timer")
    else:
        sh("systemctl", "disable", "--now", f"valheim-backup-{name}.timer", check=False, quiet=True)
    sh("systemctl", "enable", f"valheim-server-{name}", f"valheim-agent-{name}", quiet=True)
    if restart:
        say("Settings changed; restarting the server (it saves first)")
        sh("systemctl", "restart", f"valheim-server-{name}")
    else:
        sh("systemctl", "start", f"valheim-server-{name}")
    sh("systemctl", "restart", f"valheim-agent-{name}")


def uninstall(name: str, purge: bool) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        sys.exit("bad instance name")
    say(f"Removing {name}")
    units = [f"valheim-server-{name}.service", f"valheim-agent-{name}.service",
             f"valheim-backup-{name}.timer", f"valheim-backup-{name}.service"]
    sh("systemctl", "disable", "--now", *units, check=False, quiet=True)
    for unit in units:
        Path(f"/etc/systemd/system/{unit}").unlink(missing_ok=True)
    sh("systemctl", "daemon-reload")
    Path(f"/etc/sudoers.d/valheim-{name}").unlink(missing_ok=True)
    cfg_path = ETC / f"{name}.json"
    if cfg_path.exists() and shutil.which("ufw"):
        port = json.loads(cfg_path.read_text())["port"]
        sh("ufw", "delete", "allow", f"{port}:{port + 1}/udp", check=False, quiet=True)
    for suffix in ("json", "conf", "env", "agent.env"):
        (ETC / f"{name}.{suffix}").unlink(missing_ok=True)
    if purge:
        shutil.rmtree(ROOT / "instances" / name, ignore_errors=True)
    else:
        say(f"World data kept in {ROOT / 'instances' / name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    parser.add_argument("--secrets")
    parser.add_argument("--ssh-port", type=int)
    parser.add_argument("--uninstall")
    parser.add_argument("--purge", action="store_true")
    args = parser.parse_args()

    if args.uninstall:
        uninstall(args.uninstall, args.purge)
        return

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    secrets = json.loads(Path(args.secrets).read_text(encoding="utf-8"))
    if args.ssh_port:
        cfg["ssh_port"] = args.ssh_port
    preflight()
    install_packages()
    ensure_accounts()
    install_files()
    instance_dir = Path(cfg["instance_dir"])
    existed = (ETC / f"{cfg['name']}.json").exists()
    changed = render_instance(cfg, secrets)
    install_game(instance_dir)
    sh("chown", "-R", "valheim:valheim", str(instance_dir / "server"))
    configure_firewall(cfg)
    start(cfg, restart=existed and changed)
    say("Host setup complete")


if __name__ == "__main__":
    main()
