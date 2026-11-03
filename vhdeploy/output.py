"""Where progress goes. The terminal by default; a browser job when one is
running. Thread-local, because each job runs on its own thread while the
dashboard keeps serving other requests."""

from __future__ import annotations

import subprocess
import threading
from typing import Callable

_local = threading.local()


def set_sink(sink: Callable[[str], None] | None) -> None:
    _local.sink = sink


def sink() -> Callable[[str], None] | None:
    return getattr(_local, "sink", None)


def stream(argv: list[str], stdin: bytes | None = None) -> int:
    """Run a command, sending its output to wherever progress is going."""
    target = sink()
    if target is None:
        kwargs = {} if stdin is None else {"input": stdin}
        return subprocess.run(argv, check=False, **kwargs).returncode
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def feed() -> None:
        try:
            proc.stdin.write(stdin)
            proc.stdin.close()
        except OSError:
            pass  # the far end exited; its output says why

    if stdin is not None:
        threading.Thread(target=feed, daemon=True).start()
    for raw in proc.stdout:
        target(raw.decode("utf-8", "replace").rstrip())
    return proc.wait()
