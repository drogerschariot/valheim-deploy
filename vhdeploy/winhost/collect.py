"""host/agent.py's Collectors, with the Linux-only readings (/proc, /sys)
replaced by Windows API calls. Everything else is inherited unchanged."""

from __future__ import annotations

import ctypes
import os
from typing import Any

from . import IS_WINDOWS

from agent import Collectors, parse_flags  # noqa: E402


class WinCollectors(Collectors):
    BACKUP_GLOB = "*.zip"

    def __init__(self, cfg: dict[str, Any], backend: Any) -> None:
        super().__init__(cfg, backend)
        self._last_times: tuple[int, int] | None = None

    def launch_flags(self) -> dict[str, Any]:
        # The app started the process, so it knows the argv it used.
        if not self.backend.running() or not self.backend.argv:
            return {"available": False}
        return parse_flags(self.backend.argv[1:])

    def cpu_percent(self) -> float | None:
        if not IS_WINDOWS:
            try:
                return super().cpu_percent()
            except OSError:
                return None
        idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
        if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        busy_total = (kernel.value + user.value, idle.value)  # kernel time includes idle
        last, self._last_times = self._last_times, busy_total
        if last is None or busy_total[0] <= last[0]:
            return None
        total, idle_delta = busy_total[0] - last[0], busy_total[1] - last[1]
        return round(100.0 * (total - idle_delta) / total, 1)

    def vm_metrics(self) -> dict[str, Any]:
        total = used = 0
        if IS_WINDOWS:
            class Status(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            status = Status()
            status.dwLength = ctypes.sizeof(Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                total, used = status.ullTotalPhys, status.ullTotalPhys - status.ullAvailPhys
        return {
            "cpu_percent": self.cpu_percent(),
            "cpu_cores": os.cpu_count(),
            "load_average": None,  # Windows has no load average
            "memory": {"total_bytes": total or None, "used_bytes": used or None,
                       "percent": round(100 * used / total, 1) if total else None},
            "disk_io": None,
        }
