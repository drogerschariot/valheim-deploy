"""Long-running work started from the browser: creating, updating, deleting.

Each job runs on its own thread with its output captured, so the page can show
progress and a reload can pick the job back up. Only one job runs per server:
two installers racing on one machine is how a world gets damaged.
"""

from __future__ import annotations

import secrets
import threading
from collections import deque
from typing import Any, Callable

from . import output
from .ui import UI


class Busy(Exception):
    pass


class Job:
    def __init__(self, server: str, kind: str) -> None:
        self.id = secrets.token_urlsafe(9)
        self.server = server
        self.kind = kind
        self.state = "running"
        self.error = ""
        self.lines: deque[str] = deque(maxlen=4000)

    def view(self) -> dict[str, Any]:
        return {"id": self.id, "server": self.server, "kind": self.kind, "state": self.state,
                "error": self.error, "lines": list(self.lines)}


class JobUI(UI):
    """A UI with no prompts: it reads answers and writes to the job's log.
    The closing summary is skipped because it prints the join password; the
    page shows the Connect tab instead."""

    show_info = False

    def __init__(self, job: Job, answers: dict[str, Any] | None = None) -> None:
        super().__init__(answers or {})
        self.job = job

    def say(self, text: str = "") -> None:
        self.job.lines.append(text)

    heading = warn = ok = say


class Jobs:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.jobs: dict[str, Job] = {}

    def start(self, server: str, kind: str, work: Callable[[JobUI], Any],
              answers: dict[str, Any] | None = None) -> Job:
        with self.lock:
            if self.running(server):
                raise Busy(f"{server} is already busy with something else.")
            job = Job(server, kind)
            self.jobs[job.id] = job
            for old in list(self.jobs)[:-50]:
                if self.jobs[old].state != "running":
                    del self.jobs[old]

        def body() -> None:
            output.set_sink(job.lines.append)
            try:
                result = work(JobUI(job, answers))
                if result:
                    job.error = "It did not finish. See the log above."
                job.state = "failed" if result else "done"
            except SystemExit as exc:      # how providers report a clear failure
                job.error, job.state = str(exc.code), "failed"
            except Exception as exc:
                job.error, job.state = f"{type(exc).__name__}: {exc}", "failed"

        threading.Thread(target=body, daemon=True).start()
        return job

    def running(self, server: str) -> Job | None:
        return next((j for j in self.jobs.values() if j.server == server and j.state == "running"), None)

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)
