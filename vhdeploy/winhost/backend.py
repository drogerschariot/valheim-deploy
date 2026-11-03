"""One Valheim server on this PC: start, stop so it saves, restart on a crash,
daily backups. A port of host/agent.py's SupervisedBackend to Windows, with
the same state() shape so the dashboard reads both alike."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import threading
import time
import zipfile
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import IS_WINDOWS, ctrlc, game_dir

import launcher  # noqa: E402  (host/, put on the path by the package)
from agent import READY_RE  # noqa: E402

STOP_BUDGET = 120  # seconds Valheim gets to write the world; as on Linux
CREATE_NO_WINDOW = 0x08000000


def server_binary() -> list[str]:
    """The command that runs the game. Tests set VALHEIM_BINARY_ARGV to a
    JSON list, so a fake server can stand in for the real one."""
    override = os.environ.get("VALHEIM_BINARY_ARGV")
    if override:
        return json.loads(override)
    return [str(game_dir() / ("valheim_server.exe" if IS_WINDOWS else "valheim_server.x86_64"))]


def game_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("VALHEIM_SERVER_PASSWORD", "VALHEIM_AGENT_TOKEN", "VALHEIM_CONFIG_JSON")}
    # The dedicated server is app 896660 but must advertise itself as the game.
    env["SteamAppId"] = launcher.STEAM_GAME_APP_ID
    return env


def process_memory(pid: int) -> int | None:
    """Working set in bytes, from the Windows API."""
    if not IS_WINDOWS:
        try:
            for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
        except OSError:
            pass
        return None
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    *[(n, ctypes.c_size_t) for n in (
                        "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                        "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                        "PagefileUsage", "PeakPagefileUsage")]]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        if kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize)
        return None
    finally:
        kernel32.CloseHandle(handle)


class WindowsBackend:
    def __init__(self, cfg: dict[str, Any], password: str) -> None:
        self.cfg = cfg
        self.name = cfg["name"]
        self.password = password
        self.root = Path(cfg["instance_dir"])
        self.data_dir = self.root / "data"
        self.backup_dir = self.root / "backups"
        self.log_file = self.root / "server.log"
        # The game outlives the app if the app is killed, so its PID is kept
        # to find it again on the next start.
        self.pid_file = self.root / "server.pid"
        self.lock = threading.RLock()
        self.proc: subprocess.Popen | None = None
        self.argv: list[str] = []
        self.phase = "stopped"
        self.wanted = False
        self.ready = False
        self.started_at: float | None = None
        self.restarts = 0
        self.log: deque[str] = deque(maxlen=20000)
        self._closing = False
        for d in (self.data_dir, self.backup_dir):
            d.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=self._watch, daemon=True, name=f"watch-{self.name}").start()
        threading.Thread(target=self._backup_timer, daemon=True, name=f"backup-{self.name}").start()

    # -- logs --------------------------------------------------------------

    def _emit(self, line: str) -> None:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.log.append(f"{stamp} {line.rstrip()}")
        if READY_RE.search(line):
            self.ready = True

    def _tail(self, proc: subprocess.Popen) -> None:
        """Follow the game's -logFile. On Windows the server prints nothing
        useful to a console, so its log file is the only record."""
        pos = 0
        pending = ""
        while True:
            try:
                with open(self.log_file, encoding="utf-8", errors="replace") as f:
                    f.seek(0, os.SEEK_END)
                    if f.tell() < pos:
                        pos = 0  # a new run truncated it
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
            except OSError:
                chunk = ""
            if chunk:
                pending += chunk
                *lines, pending = pending.split("\n")
                for line in lines:
                    if line.strip():
                        self._emit(line)
            if proc.poll() is not None and not chunk:
                if pending.strip():
                    self._emit(pending)
                return
            time.sleep(0.5)

    # -- control -----------------------------------------------------------

    @staticmethod
    def installed_game() -> bool:
        return bool(os.environ.get("VALHEIM_BINARY_ARGV")) or Path(server_binary()[0]).exists()

    def installed(self) -> bool:
        return self.installed_game()

    def _start(self) -> None:
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return
            if not self.installed():
                self.phase = "not installed"
                return
            self._stop_leftover()
            self.ready = False
            command = server_binary()
            game_args = launcher.build_argv(self.cfg, self.password, command[-1])[1:]
            self.argv = [*command, "-nographics", "-batchmode", *game_args, "-logFile", str(self.log_file)]
            try:
                self.log_file.unlink()
            except OSError:
                pass
            cwd = game_dir() if game_dir().is_dir() else self.root
            # Its own hidden console, so it can be sent Ctrl+C; see ctrlc.py.
            flags = CREATE_NO_WINDOW if IS_WINDOWS else 0
            self.proc = subprocess.Popen(
                self.argv, cwd=str(cwd), env=game_env(), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
            self.started_at = time.time()
            self.phase = "starting"
            self.pid_file.write_text(str(self.proc.pid))
            self._emit(f"Started {self.name} (process {self.proc.pid}).")
        threading.Thread(target=self._tail, args=(self.proc,), daemon=True).start()

    def _stop(self) -> None:
        with self.lock:
            proc = self.proc
        if not proc or proc.poll() is not None:
            return
        self.phase = "stopping"
        self._emit("Stopping: asking the server to save the world first...")
        try:
            delivered = ctrlc.interrupt(proc)
        except (OSError, subprocess.SubprocessError) as exc:
            delivered = False
            self._emit(f"Could not ask it to save: {exc}")
        try:
            proc.wait(STOP_BUDGET if delivered else 5)
            self.pid_file.unlink(missing_ok=True)
            self._emit("The server saved and stopped.")
        except subprocess.TimeoutExpired:
            # Being killed mid-write is worse than waiting, hence the budget.
            self._emit(f"It did not stop within {STOP_BUDGET}s, so it was ended. "
                       "Progress since the last autosave may be lost.")
            proc.kill()
            proc.wait()

    def _stop_leftover(self) -> None:
        """A server left running by an app that was killed, rather than quit,
        holds the game ports, so a new one could never start. Stop it the safe
        way, so its world is saved, before doing anything else."""
        try:
            pid = int(self.pid_file.read_text().strip())
        except (OSError, ValueError):
            return
        # Windows reports the executable; elsewhere the whole command line.
        program = Path(server_binary()[0 if IS_WINDOWS else -1]).name
        if ctrlc.alive(pid) and program.lower() in ctrlc.image(pid).lower():
            self._emit(f"Found {self.name} still running from before (process {pid}). "
                       "Stopping it so it saves...")
            ctrlc.interrupt_pid(pid)
            deadline = time.time() + STOP_BUDGET
            while ctrlc.alive(pid) and time.time() < deadline:
                time.sleep(1)
            self._emit("It stopped." if not ctrlc.alive(pid) else
                       f"It did not stop within {STOP_BUDGET}s; end valheim_server.exe in Task Manager.")
        self.pid_file.unlink(missing_ok=True)

    def _watch(self) -> None:
        while not self._closing:
            time.sleep(2)
            with self.lock:
                proc = self.proc
                dead = proc is not None and proc.poll() is not None
            if not dead:
                if proc is not None and self.ready and self.phase == "starting":
                    self.phase = "running"
                continue
            if self.wanted and self.phase != "stopping":
                self._emit(f"The server exited (code {proc.returncode}); restarting in 20s.")
                self.restarts += 1
                with self.lock:
                    self.proc = None
                time.sleep(20)
                if self.wanted:
                    self._start()
            elif self.phase not in ("stopping", "not installed"):
                self.phase = "stopped"

    def _backup_timer(self) -> None:
        while not self._closing:
            hour, minute = (int(x) for x in self.cfg["backup_time"].split(":"))
            now = datetime.now()
            due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if due <= now:
                due += timedelta(days=1)
            time.sleep(min((due - now).total_seconds(), 3600))
            if self.cfg["backup_enabled"] and datetime.now() >= due:
                self._emit(self.backup()[1])

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def control(self, action: str) -> tuple[bool, str]:
        if action == "start":
            self.wanted = True
            self._start()
            if self.phase == "not installed":
                return False, "The game is not installed yet. Use Update game."
            return True, "Starting. It is ready when the status says running."
        if action == "stop":
            self.wanted = False
            self._stop()
            self.phase = "stopped"
            return True, "Saved and stopped."
        if action == "restart":
            self.wanted = False
            self._stop()
            self.wanted = True
            self._start()
            return True, "Restarting."
        return False, "unknown action"

    def close(self) -> None:
        """Stop for good, saving first. Used on Quit."""
        self.wanted = False
        self._stop()
        self.phase = "stopped"
        self._closing = True

    # -- backups -----------------------------------------------------------

    def backup(self) -> tuple[bool, str]:
        """Zip the world into backups/ and keep the newest backup_retain.

        Prefers Valheim's own newest auto-backup over the live world: the game
        wrote it, so it is complete, while the live files may be mid-save."""
        world, worlds = self.cfg["world_name"], self.data_dir / "worlds_local"
        autos = sorted(worlds.glob(f"{world}_backup_auto-*")) if worlds.is_dir() else []
        files: list[Path] = []
        if autos and autos[-1].is_dir():
            files = [p for p in autos[-1].rglob("*") if p.is_file()]
            base, note = autos[-1], "the server's own latest backup"
        elif (worlds / world).is_dir():
            files = [p for p in (worlds / world).rglob("*") if p.is_file()]
            base, note = worlds / world, "the live world"
        else:
            files = [p for p in (worlds.glob(f"{world}.*") if worlds.is_dir() else []) if p.is_file()]
            base, note = worlds, "the live world"
        if not files:
            return False, (f"No world called {world} has been saved yet. A new world is written at "
                           "its first save, a few minutes after the server starts.")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.backup_dir / f"{world}-{stamp}.zip"
        tmp = target.with_suffix(".zip.part")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for path in files:
                z.write(path, Path(world) / path.relative_to(base))
        os.replace(tmp, target)
        archives = sorted(self.backup_dir.glob(f"{world}-*.zip"))
        for old in archives[:-int(self.cfg["backup_retain"])]:
            old.unlink(missing_ok=True)
        return True, f"Backed up {world} from {note} to {target.name}."

    def update_check(self) -> tuple[bool, str]:
        return True, "Use Update game: it stops your servers, updates from Steam, and starts them again."

    # -- state ---------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        running = self.running()
        active = "active" if running else ("failed" if self.phase == "not installed" else "inactive")
        return {
            "active": active,
            "sub": self.phase,
            "pid": str(self.proc.pid) if running else None,
            "started": None,
            "uptime_seconds": int(time.time() - self.started_at) if running and self.started_at else None,
            "memory_bytes": process_memory(self.proc.pid) if running else None,
            "restarts": str(self.restarts),
            "ready": running and self.ready,
        }

    def logs(self, lines: int) -> list[str]:
        return list(self.log)[-lines:]
