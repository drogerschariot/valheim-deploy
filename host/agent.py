#!/usr/bin/env python3
"""The per-server agent: a JSON API over one Valheim instance.

The browser never talks to this. The dashboard on the owner's own machine does,
through an SSH tunnel (or Docker's loopback port), so this binds to loopback
and is never exposed to a network. The admin console stays off the internet
without anyone having to get a firewall rule right.

Standard library only, on purpose: every dependency here would be one more
thing to patch on a host reachable from the internet.

Two rules govern the collectors:

  * Every collector degrades to a null or unknown field rather than raising.
    The point of a dashboard is to be readable when the server is sick.

  * Nothing slow runs inside a request handler without a cache in front of it.
    An uncached page would run a dozen processes per poll per open tab against
    the disk the server is saving to.

There is no RCON in Valheim and no working stdin command channel either. A
Force Save button was built on stdin and removed after two live tests against
Valheim l-1.0.12 (plain pipe and a real pty, with and without -console) found
the binary never acts on it; only the SIGINT shutdown save fires. Do not
re-attempt that without new information. Kick and ban go through the list
files, which do not depend on any of this.

Two runtimes share everything above the Backend line:

  * SystemdBackend -- a Linux host, where the agent is a neighbour of the game.
  * SupervisedBackend -- a Docker container, where the agent is PID 1's child
    and runs the game itself, because there is no systemd or journal.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import a2s  # noqa: E402
import launcher  # noqa: E402

SNAP_TTL = 4.0
SLOW_TTL = 120.0
READY_RE = re.compile(r"Game server connected|DungeonDB Start")
LIST_ENTRY = re.compile(r"^[A-Za-z0-9]+_[0-9]+$")  # Steam_76561198000000000

# Actions that change something. CONFIRM_REQUIRED ones return 428 until the
# client re-sends with confirm true, so a stray click cannot stop a server
# people are playing on.
ACTIONS = {"start", "stop", "restart", "backup", "update_check"}
CONFIRM_REQUIRED = {"stop", "restart"}

STEAM_DIR = Path(os.environ.get("VALHEIM_STEAM_DIR", "/opt/valheim/steamcmd"))
BIN_DIR = Path(os.environ.get("VALHEIM_BIN_DIR", "/opt/valheim/bin"))


def run(argv: list[str], timeout: int = 15) -> tuple[int, str, str]:
    """Run a command, never raise. Returns (rc, stdout, stderr)."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 255, "", f"{type(exc).__name__}: {exc}"


class Cached:
    """A value refreshed at most once per ttl, shared across threads."""

    def __init__(self, ttl: float, producer: Callable[[], Any]) -> None:
        self._ttl = ttl
        self._producer = producer
        self._lock = threading.Lock()
        self._value: Any = None
        self._at = 0.0

    def get(self, force: bool = False) -> Any:
        with self._lock:
            if force or self._value is None or time.time() - self._at > self._ttl:
                try:
                    self._value = self._producer()
                except Exception as exc:  # a collector must never break a page
                    self._value = {"error": f"{type(exc).__name__}: {exc}"}
                self._at = time.time()
            return self._value


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------

class SystemdBackend:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self.name = cfg["name"]
        self.unit = f"valheim-server-{self.name}"

    def state(self) -> dict[str, Any]:
        props = ["ActiveState", "SubState", "MainPID", "ActiveEnterTimestamp",
                 "ActiveEnterTimestampMonotonic", "MemoryCurrent", "NRestarts",
                 "InvocationID"]
        rc, out, _ = run(["systemctl", "show", self.unit, "--no-pager"] + [f"-p{p}" for p in props])
        values: dict[str, str] = {}
        if rc == 0:
            for line in out.splitlines():
                key, _, value = line.partition("=")
                values[key] = value

        # Measured on the monotonic clock. The formatted timestamp carries the
        # host's local zone name, which strptime cannot resolve in general.
        uptime = None
        mono = values.get("ActiveEnterTimestampMonotonic", "")
        if values.get("ActiveState") == "active" and mono.isdigit() and int(mono):
            try:
                booted = float(Path("/proc/uptime").read_text().split()[0])
                uptime = max(0, int(booted - int(mono) / 1_000_000))
            except (OSError, ValueError, IndexError):
                pass

        memory = values.get("MemoryCurrent", "")
        return {
            "active": values.get("ActiveState", "unknown"),
            "sub": values.get("SubState", "unknown"),
            "pid": values.get("MainPID") or None,
            "started": values.get("ActiveEnterTimestamp") or None,
            "uptime_seconds": uptime,
            "memory_bytes": int(memory) if memory.isdigit() else None,
            "restarts": values.get("NRestarts"),
            "ready": self._ready(values.get("InvocationID", "")),
        }

    def _ready(self, invocation: str) -> bool:
        # Bounded to this run by invocation ID: a restarted server's journal
        # still holds the readiness line from its previous run, which would
        # report a failed start as healthy.
        if not invocation:
            return False
        rc, out, _ = run(["journalctl", f"_SYSTEMD_INVOCATION_ID={invocation}",
                          "--no-pager", "-o", "cat"], timeout=20)
        return rc == 0 and bool(READY_RE.search(out))

    def logs(self, lines: int) -> list[str]:
        rc, out, err = run(["journalctl", "-u", self.unit, "--no-pager", "-n", str(lines),
                            "-o", "short-iso"], timeout=25)
        if rc != 0:
            return [f"journalctl unavailable: {err.strip() or rc}"]
        return [re.sub(r"\s\S+\[\d+\]:", "", line, count=1) for line in out.splitlines()]

    def control(self, action: str) -> tuple[bool, str]:
        rc, out, err = run(["sudo", "-n", "/usr/bin/systemctl", action, self.unit], timeout=180)
        ok = rc == 0
        return ok, (err or out or ("done" if ok else f"exit {rc}")).strip()

    def backup(self) -> tuple[bool, str]:
        # As the valheim user, not root: root-owned archives would be debris
        # the script's own pruning cannot manage.
        rc, out, err = run(["sudo", "-n", "-u", "valheim", str(BIN_DIR / "valheim-backup.sh"),
                            self.name], timeout=300)
        return rc == 0, (out or err).strip()

    def update_check(self) -> tuple[bool, str]:
        rc, out, err = run(["sudo", "-n", str(BIN_DIR / "valheim-update.sh"), self.name, "--check"],
                           timeout=180)
        return rc == 0, (out or err).strip()


class SupervisedBackend:
    """Runs the game as a child process. Used inside the Docker image."""

    STOP_BUDGET = 120

    def __init__(self, cfg: dict[str, Any], password: str) -> None:
        self.cfg = cfg
        self.name = cfg["name"]
        self.password = password
        self.lock = threading.RLock()
        self.proc: subprocess.Popen[str] | None = None
        self.phase = "installing"
        self.wanted = True
        self.ready = False
        self.started_at: float | None = None
        self.restarts = 0
        self.log: deque[str] = deque(maxlen=20000)
        self.server_dir = Path(cfg["instance_dir"]) / "server"
        self.binary = os.environ.get("VALHEIM_BINARY") or str(self.server_dir / "valheim_server.x86_64")

    def _emit(self, line: str) -> None:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.log.append(f"{stamp} {line.rstrip()}")
        if READY_RE.search(line):
            self.ready = True
        print(line.rstrip(), flush=True)

    def _pump(self, stream: Any) -> None:
        for line in stream:
            self._emit(line)

    def boot(self) -> None:
        """Install or update, then start. Runs on every container start, which
        is what makes `valheim update` a restart: the server must track the
        public build or Steam-updated clients are locked out."""
        try:
            self._steam_update()
        except Exception as exc:
            self._emit(f"steam update failed: {exc}")
        if self.wanted:
            self._start()
        threading.Thread(target=self._watch, daemon=True).start()
        threading.Thread(target=self._backup_timer, daemon=True).start()

    def _steam_update(self) -> None:
        if os.environ.get("VALHEIM_SKIP_STEAM"):
            return
        if Path(self.binary).exists() and any(
                (Path(self.cfg["instance_dir"]) / "data" / "worlds_local").glob("*")
                if (Path(self.cfg["instance_dir"]) / "data" / "worlds_local").is_dir() else []):
            self._emit("Taking a pre-update backup...")
            self._emit(self.backup()[1])
        argv = [str(STEAM_DIR / "steamcmd.sh"), "+force_install_dir", str(self.server_dir),
                "+login", "anonymous", "+app_update", "896660", "validate", "+quit"]
        # steamcmd commonly exits non-zero on its first self-updating run.
        for attempt in range(1, 4):
            self._emit(f"Installing the Valheim server from Steam (attempt {attempt})...")
            p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 errors="replace", env={**os.environ, "HOME": str(STEAM_DIR)})
            self._pump(p.stdout)
            if p.wait() == 0 and Path(self.binary).exists():
                return
            time.sleep(10)
        raise RuntimeError("SteamCMD did not install the server")

    def _start(self) -> None:
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return
            if not Path(self.binary).exists():
                self.phase = "not installed"
                return
            self.ready = False
            argv = launcher.build_argv(self.cfg, self.password, self.binary)
            self.proc = subprocess.Popen(
                argv, cwd=self.server_dir if self.server_dir.is_dir() else None,
                env=launcher.game_env(self.cfg), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
            )
            self.started_at = time.time()
            self.phase = "running"
        threading.Thread(target=self._pump, args=(self.proc.stdout,), daemon=True).start()

    def _stop(self) -> None:
        with self.lock:
            proc = self.proc
        if not proc or proc.poll() is not None:
            return
        # Valheim saves the world only on SIGINT. Being killed mid-write is
        # worse than waiting, so the full budget applies.
        self.phase = "stopping"
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(self.STOP_BUDGET)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def _watch(self) -> None:
        while True:
            time.sleep(5)
            with self.lock:
                dead = self.proc is not None and self.proc.poll() is not None
                restart = dead and self.wanted
            if restart:
                self._emit("Server exited; restarting in 20s.")
                self.restarts += 1
                time.sleep(20)
                if self.wanted:
                    self._start()
            elif dead:
                self.phase = "stopped"

    def _backup_timer(self) -> None:
        """systemd's OnCalendar equivalent for a host with no systemd."""
        hour, minute = (int(x) for x in self.cfg["backup_time"].split(":"))
        while self.cfg["backup_enabled"]:
            now = datetime.now()
            due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if due <= now:
                due += timedelta(days=1)
            time.sleep((due - now).total_seconds())
            self._emit(self.backup()[1])

    def shutdown(self, *_: Any) -> None:
        self.wanted = False
        self._stop()
        os._exit(0)

    def state(self) -> dict[str, Any]:
        running = self.proc is not None and self.proc.poll() is None
        memory = None
        if running:
            try:
                status = Path(f"/proc/{self.proc.pid}/status").read_text()
                m = re.search(r"VmRSS:\s+(\d+) kB", status)
                memory = int(m.group(1)) * 1024 if m else None
            except OSError:
                pass
        return {
            "active": "active" if running else ("activating" if self.phase == "installing" else "inactive"),
            "sub": self.phase,
            "pid": str(self.proc.pid) if running else None,
            "started": None,
            "uptime_seconds": int(time.time() - self.started_at) if running and self.started_at else None,
            "memory_bytes": memory,
            "restarts": str(self.restarts),
            "ready": running and self.ready,
        }

    def logs(self, lines: int) -> list[str]:
        return list(self.log)[-lines:]

    def control(self, action: str) -> tuple[bool, str]:
        if action == "start":
            self.wanted = True
            self._start()
        elif action == "stop":
            self.wanted = False
            self._stop()
            self.phase = "stopped"
        elif action == "restart":
            self.wanted = False
            self._stop()
            self.wanted = True
            self._start()
        return True, "done"

    def backup(self) -> tuple[bool, str]:
        rc, out, err = run([str(BIN_DIR / "valheim-backup.sh"), self.name], timeout=300)
        return rc == 0, (out or err).strip()

    def update_check(self) -> tuple[bool, str]:
        rc, out, err = run([str(BIN_DIR / "valheim-update.sh"), self.name, "--check"], timeout=180)
        return rc == 0, (out or err).strip()


# --------------------------------------------------------------------------
# Collectors
# --------------------------------------------------------------------------

PLAYER_RE = re.compile(r"Got character ZDOID from (.+?) : ")
CONNECT_RE = re.compile(r"Got connection SteamID (\d+)")
SAVE_RE = re.compile(r"World save \(5/5\) done\. Total time \[(\d+)ms\]")
HEARTBEAT_RE = re.compile(r"Connections (\d+) ZDOS:(\d+)\s+sent:(\d+) recv:(\d+)")
JOIN_CODE_RE = re.compile(r"join code (\d+)", re.IGNORECASE)


def parse_activity(lines: list[str]) -> dict[str, Any]:
    """Recent players, last save, and the server's own traffic counters.

    Player names are "recent" rather than "online" on purpose. Valheim logs a
    join but nothing reliable on leave, so this is the last few people seen,
    not a current roster. The authoritative count comes from A2S.
    """
    recent: list[dict[str, str]] = []
    last_save: dict[str, Any] | None = None
    heartbeat: dict[str, int] | None = None
    connections: list[str] = []
    join_code = None

    for line in lines:
        stamp = line.split(" ", 1)[0]
        m = PLAYER_RE.search(line)
        if m:
            name = m.group(1).strip()
            recent = [r for r in recent if r["name"] != name]
            recent.append({"name": name, "at": stamp})
            continue
        m = CONNECT_RE.search(line)
        if m:
            connections.append(m.group(1))
            continue
        m = SAVE_RE.search(line)
        if m:
            last_save = {"at": stamp, "duration_ms": int(m.group(1))}
            continue
        m = HEARTBEAT_RE.search(line)
        if m:
            heartbeat = {"connections": int(m.group(1)), "zdos": int(m.group(2)),
                         "sent": int(m.group(3)), "recv": int(m.group(4))}
            continue
        m = JOIN_CODE_RE.search(line)
        if m:
            join_code = m.group(1)

    return {
        "recent_players": list(reversed(recent))[:10],
        "last_save": last_save,
        "heartbeat": heartbeat,
        "unique_steam_ids": len(set(connections)),
        "join_code": join_code,
    }


def parse_flags(args: list[str]) -> dict[str, Any]:
    """Launch flags from an argv, with the password removed."""
    flags: dict[str, Any] = {}
    modifiers: dict[str, str] = {}
    keys: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-password":
            flags["password"] = "(hidden)"  # never expose it, even to an admin
            i += 2
        elif a == "-modifier" and i + 2 < len(args):
            modifiers[args[i + 1]] = args[i + 2]
            i += 3
        elif a == "-setkey" and i + 1 < len(args):
            keys.append(args[i + 1])
            i += 2
        elif a.startswith("-"):
            nxt = args[i + 1] if i + 1 < len(args) else ""
            if nxt and not (nxt.startswith("-") and not nxt[1:].isdigit()):
                flags[a.lstrip("-")] = nxt
                i += 2
            else:
                flags[a.lstrip("-")] = True
                i += 1
        else:
            i += 1
    return {"available": True, "flags": flags, "modifiers": modifiers, "keys": keys}


class Collectors:
    def __init__(self, cfg: dict[str, Any], backend: Any) -> None:
        self.cfg = cfg
        self.backend = backend
        root = Path(cfg["instance_dir"])
        self.root = root
        self.world_dir = root / "data" / "worlds_local"
        self.backup_dir = root / "backups"
        self.list_files = {
            "admins": root / "data" / "adminlist.txt",
            "banned": root / "data" / "bannedlist.txt",
            "permitted": root / "data" / "permittedlist.txt",
        }
        self._last_cpu: tuple[int, int] | None = None
        self._last_disk: tuple[float, int, int] | None = None
        self.service = Cached(SNAP_TTL, backend.state)
        self.query = Cached(SNAP_TTL, lambda: a2s.query("127.0.0.1", int(cfg["port"]) + 1))
        self.activity = Cached(SNAP_TTL * 3, lambda: parse_activity(backend.logs(4000)))
        self.flags = Cached(SLOW_TTL, self.launch_flags)
        self.world = Cached(SLOW_TTL, self.world_files)
        self.backups = Cached(SLOW_TTL, self.list_backups)
        self.disk = Cached(SLOW_TTL, self.disk_usage)
        # Same TTL as the fast group: CPU% and disk I/O are rates, and a rate
        # sampled every 120s is nearly meaningless to someone watching.
        self.vm = Cached(SNAP_TTL, self.vm_metrics)

    def launch_flags(self) -> dict[str, Any]:
        """The flags the running process actually has.

        Read from /proc rather than config: this reports what is running, not
        what was last deployed. The two differ until a change is applied.
        """
        pid = self.service.get().get("pid")
        if not pid or pid == "0":
            return {"available": False}
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return {"available": False}
        return parse_flags([a.decode("utf-8", "replace") for a in raw.split(b"\x00") if a])

    def world_files(self) -> dict[str, Any]:
        """Size of the *live* world only.

        Valheim's rotating auto-backups sit alongside it as full copies named
        <world>_backup_auto-<timestamp>, so those are skipped; otherwise the
        reported size grows with every backup rather than with the world.
        """
        total = 0
        entries: list[dict[str, Any]] = []
        if self.world_dir.is_dir():
            for child in sorted(self.world_dir.iterdir()):
                if not child.is_dir() or "_backup_auto-" in child.name:
                    continue
                for path in child.rglob("*"):
                    try:
                        if not path.is_file():
                            continue
                        st = path.stat()
                    except OSError:
                        continue
                    total += st.st_size
                    if path.suffix in (".db2", ".fwl2"):
                        entries.append({
                            "name": str(path.relative_to(self.world_dir)),
                            "bytes": st.st_size,
                            "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc)
                            .isoformat(timespec="seconds"),
                        })
        entries.sort(key=lambda e: e["modified"], reverse=True)
        return {"total_bytes": total, "files": entries[:12]}

    BACKUP_GLOB = "*.tar.gz"

    def list_backups(self) -> list[dict[str, Any]]:
        out = []
        if self.backup_dir.is_dir():
            for path in sorted(self.backup_dir.glob(self.BACKUP_GLOB), reverse=True):
                try:
                    st = path.stat()
                except OSError:
                    continue
                out.append({
                    "name": path.name,
                    "bytes": st.st_size,
                    "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc)
                    .isoformat(timespec="seconds"),
                })
        return out

    def disk_usage(self) -> dict[str, Any]:
        usage = shutil.disk_usage(self.root)
        return {"total": usage.total, "used": usage.used, "free": usage.free,
                "percent": round(usage.used / usage.total * 100, 1)}

    # Host metrics read /proc and /sys, standard on any Linux. CPU% and disk
    # I/O are deltas, so each returns null on its first call before there is a
    # sample to diff against. That is normal right after a restart, not a fault.
    def cpu_percent(self) -> float | None:
        with open("/proc/stat", encoding="utf-8") as f:
            values = [int(x) for x in f.readline().split()[1:]]
        idle, total = values[3] + (values[4] if len(values) > 4 else 0), sum(values)
        last, self._last_cpu = self._last_cpu, (idle, total)
        if last is None or total <= last[1]:
            return None
        return round(100.0 * ((total - last[1]) - (idle - last[0])) / (total - last[1]), 1)

    def disk_io(self) -> dict[str, float] | None:
        # /sys/block lists whole disks only, so summing it cannot double-count
        # a device and its partition the way /proc/diskstats can. These
        # counters are 512-byte sectors whatever the device's real block size.
        read = write = 0
        try:
            for name in os.listdir("/sys/block"):
                if name.startswith(("loop", "ram", "sr")):
                    continue
                try:
                    fields = (Path("/sys/block") / name / "stat").read_text().split()
                except OSError:
                    continue
                if len(fields) >= 7:
                    read += int(fields[2])
                    write += int(fields[6])
        except OSError:
            pass
        now = time.time()
        last, self._last_disk = self._last_disk, (now, read, write)
        if last is None or now <= last[0]:
            return None
        return {"read_bytes_per_sec": max(0.0, (read - last[1]) * 512 / (now - last[0])),
                "write_bytes_per_sec": max(0.0, (write - last[2]) * 512 / (now - last[0]))}

    def vm_metrics(self) -> dict[str, Any]:
        mem: dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                key, _, rest = line.partition(":")
                parts = rest.split()
                if parts and parts[0].isdigit():
                    mem[key] = int(parts[0]) * 1024
        total = mem.get("MemTotal", 0)
        used = max(0, total - mem.get("MemAvailable", mem.get("MemFree", 0)))
        load = os.getloadavg()
        return {
            "cpu_percent": self.cpu_percent(),
            "cpu_cores": os.cpu_count(),
            "load_average": {"1m": round(load[0], 2), "5m": round(load[1], 2), "15m": round(load[2], 2)},
            "memory": {"total_bytes": total, "used_bytes": used,
                       "percent": round(100 * used / total, 1) if total else None},
            "disk_io": self.disk_io(),
        }

    def read_list(self, name: str) -> list[str]:
        try:
            text = self.list_files[name].read_text(encoding="utf-8")
        except OSError:
            return []
        return [ln.strip() for ln in text.splitlines()
                if ln.strip() and not ln.strip().startswith("//") and not ln.strip().startswith("#")]

    def write_list(self, name: str, entries: list[str]) -> tuple[bool, str]:
        path = self.list_files[name]
        try:
            # Through a temporary file in the same directory, so a partial
            # write can never leave the server with a truncated admin list.
            tmp = path.with_suffix(path.suffix + ".new")
            tmp.write_text("\n".join(entries) + ("\n" if entries else ""), encoding="utf-8")
            os.chmod(tmp, 0o660)
            os.replace(tmp, path)
            return True, ""
        except OSError as exc:
            return False, str(exc)

    def snapshot(self) -> dict[str, Any]:
        return {
            "service": self.service.get(),
            "query": self.query.get(),
            "activity": self.activity.get(),
            "launch": self.flags.get(),
            "world": self.world.get(),
            "disk": self.disk.get(),
            "vm": self.vm.get(),
            "lists": {name: self.read_list(name) for name in self.list_files},
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }


def handle(c: Collectors, backend: Any, method: str, path: str, query: dict[str, list[str]],
           body: dict[str, Any]) -> tuple[int, Any]:
    """One API request, minus HTTP. The Windows app serves the same API
    in-process by calling this directly."""
    if method == "GET":
        if path == "/api/status":
            return HTTPStatus.OK, c.snapshot()
        if path == "/api/logs":
            try:
                n = min(int(query.get("n", ["200"])[0]), 1000)
            except ValueError:
                n = 200
            return HTTPStatus.OK, {"lines": backend.logs(n)}
        if path == "/api/backups":
            return HTTPStatus.OK, {"backups": c.backups.get()}
        return HTTPStatus.NOT_FOUND, {"error": "not found"}

    if path == "/api/action":
        action = str(body.get("action", ""))
        if action not in ACTIONS:
            return HTTPStatus.BAD_REQUEST, {"error": "unknown action"}
        if action in CONFIRM_REQUIRED and not body.get("confirm"):
            return HTTPStatus.PRECONDITION_REQUIRED, {
                "error": "confirmation required",
                "action": action,
                "detail": "Stopping saves the world first, which can take a minute. "
                          "Anyone connected is disconnected.",
            }
        if action in {"start", "stop", "restart"}:
            ok, detail = backend.control(action)
        elif action == "backup":
            ok, detail = backend.backup()
            c.backups.get(force=True)
        else:
            ok, detail = backend.update_check()
        c.service.get(force=True)
        c.flags.get(force=True)
        return (HTTPStatus.OK if ok else HTTPStatus.INTERNAL_SERVER_ERROR), {"ok": ok, "detail": detail}

    if path == "/api/list":
        name = str(body.get("list", ""))
        entry = str(body.get("entry", "")).strip()
        op = str(body.get("op", ""))
        if name not in c.list_files or op not in {"add", "remove"}:
            return HTTPStatus.BAD_REQUEST, {"error": "bad request"}
        if not LIST_ENTRY.match(entry):
            return HTTPStatus.BAD_REQUEST, {"error": "entries look like Steam_76561198000000000"}
        entries = c.read_list(name)
        if op == "add" and entry not in entries:
            entries.append(entry)
        elif op == "remove":
            entries = [e for e in entries if e != entry]
        ok, err = c.write_list(name, entries)
        return ((HTTPStatus.OK if ok else HTTPStatus.INTERNAL_SERVER_ERROR),
                {"ok": ok, "error": err, "entries": c.read_list(name)})

    return HTTPStatus.NOT_FOUND, {"error": "not found"}


def make_handler(token: str, c: Collectors, backend: Any) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "valheim-agent"
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:
            pass

        def _json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authed(self) -> bool:
            supplied = self.headers.get("Authorization", "")
            if hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
                return True
            time.sleep(1)  # blunt the obvious guessing loop
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "bad token"})
            return False

        def _body(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if 0 < length <= 64 * 1024:
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    return body if isinstance(body, dict) else {}
            except (ValueError, OSError):
                pass
            return {}

        def _serve(self, method: str) -> None:
            if not self._authed():
                return
            route = urlparse(self.path)
            body = self._body() if method == "POST" else {}
            self._json(*handle(c, backend, method, route.path, parse_qs(route.query), body))

        def do_GET(self) -> None:  # noqa: N802
            self._serve("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._serve("POST")

    return Handler


def main() -> int:
    if len(sys.argv) < 2 or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", sys.argv[1]):
        raise SystemExit("usage: agent.py <instance> [--supervise]")
    name = sys.argv[1]
    config_dir = Path(os.environ.get("VALHEIM_CONFIG_DIR", "/etc/valheim"))
    cfg = json.loads((config_dir / f"{name}.json").read_text(encoding="utf-8"))
    token = os.environ.pop("VALHEIM_AGENT_TOKEN", "").strip()
    if len(token) < 16:
        raise SystemExit("VALHEIM_AGENT_TOKEN is unset or short; refusing to start.")

    supervise = "--supervise" in sys.argv
    if supervise:
        password = os.environ.get("VALHEIM_SERVER_PASSWORD", "")
        if not password:
            raise SystemExit("VALHEIM_SERVER_PASSWORD is unset; refusing to start.")
        backend: Any = SupervisedBackend(cfg, password)
        signal.signal(signal.SIGTERM, backend.shutdown)
        signal.signal(signal.SIGINT, backend.shutdown)
        threading.Thread(target=backend.boot, daemon=True).start()
    else:
        backend = SystemdBackend(cfg)

    # Loopback on a host. Inside a container it has to be the container's own
    # interface for the port publish to work; the CLI publishes that port on
    # the machine's 127.0.0.1 only.
    bind = "0.0.0.0" if supervise else "127.0.0.1"
    bind = os.environ.get("VALHEIM_AGENT_BIND", bind)
    port = int(os.environ.get("VALHEIM_AGENT_PORT") or (8080 if supervise else cfg["agent_port"]))
    server = ThreadingHTTPServer((bind, port), make_handler(token, Collectors(cfg, backend), backend))
    server.daemon_threads = True
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
