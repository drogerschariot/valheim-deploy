"""Stopping valheim_server.exe so that it saves.

Valheim writes the world only when it gets Ctrl+C. Windows has no signal to
send another process; the only way is to join its console and raise Ctrl+C on
that console. That is done in a short-lived helper process, never in the app
itself, for two reasons:

  * a process can be attached to one console at a time, so stopping several
    servers at once needs one helper each;
  * the helper has to ignore the Ctrl+C it raises, and that ignore flag is
    inherited by child processes. Set in the app, every server started later
    would ignore Ctrl+C too, and could then never be stopped safely.

The game must not be started with CREATE_NEW_PROCESS_GROUP, which turns Ctrl+C
off for it.
"""

from __future__ import annotations

import ctypes
import signal
import subprocess
import sys

CTRL_C_EVENT = 0
DETACHED_PROCESS = 0x00000008


def helper_argv(pid: int) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--send-ctrl-c", str(pid)]
    return [sys.executable, "-m", "vhdeploy.winhost.ctrlc", str(pid)]


def interrupt(proc: subprocess.Popen) -> bool:
    """Ask a server to save and exit. True if the request was delivered."""
    if sys.platform != "win32":
        proc.send_signal(signal.SIGINT)
        return True
    from .. import paths
    done = subprocess.run(helper_argv(proc.pid), creationflags=DETACHED_PROCESS, timeout=30,
                          cwd=str(paths.bundle_root()), check=False)
    return done.returncode == 0


def send(pid: int) -> int:
    """The helper itself. Exit code 0 means Ctrl+C was raised on pid's console."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.FreeConsole()
    if not kernel32.AttachConsole(pid):
        return 2
    kernel32.SetConsoleCtrlHandler(None, True)  # this helper ignores it
    ok = kernel32.GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0)
    kernel32.FreeConsole()
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(send(int(sys.argv[1])))
