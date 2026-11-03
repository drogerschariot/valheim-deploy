"""Talks to a server's agent, wherever it lives."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .ssh import Tunnels


class AgentError(Exception):
    pass


class AgentClient:
    def __init__(self, tunnels: Tunnels) -> None:
        self.tunnels = tunnels

    def _port(self, rec: dict[str, Any]) -> int:
        if rec["provider"] == "local":
            return rec["settings"]["port"] + 16000
        return self.tunnels.port_for(rec)

    def call(self, rec: dict[str, Any], path: str, body: dict | None = None,
             timeout: float = 200) -> tuple[int, Any]:
        try:
            port = self._port(rec)
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}",
                data=None if body is None else json.dumps(body).encode(),
                headers={"Authorization": f"Bearer {rec['secrets']['agent_token']}",
                         "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read())
            except ValueError:
                return exc.code, {"error": f"HTTP {exc.code}"}
        except (ConnectionError, OSError, ValueError) as exc:
            if rec["provider"] != "local":
                self.tunnels.drop(rec["name"])
            raise AgentError(str(exc)) from exc
