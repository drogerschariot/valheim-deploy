"""The dashboard: one page, on the owner's own machine, for all their servers.

The browser only ever talks to this process, on loopback. This process talks to
each server's agent, through an SSH tunnel for remote hosts. Agent tokens and
SSH keys therefore never reach the browser, and no admin port is opened on any
game host.

Loopback is not sufficient protection by itself: any website the owner visits
can send requests to 127.0.0.1. So there is a sign-in token, a SameSite=Strict
session cookie, and a Host header check that stops DNS rebinding.
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
import threading
import webbrowser
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import agent_client, host_a2s, registry, ssh
from .providers import get as provider

STATIC_DIR = Path(__file__).resolve().parent / "static"
# A fixed set, not a filename pattern. Making something readable before sign-in
# should take a code change.
PUBLIC_ASSETS = {"login.css"}
STATIC_ASSETS = {"app.js", "app.css", "login.css"}
SERVER_ROUTE = re.compile(r"^/api/servers/([a-z0-9-]+)/(status|logs|backups|connect|action|list|reach)$")


def dashboard_token() -> str:
    path = registry.home() / "dashboard.token"
    if not path.exists():
        registry.write_private(path, secrets.token_urlsafe(24))
    return path.read_text().strip()


def summary(rec: dict[str, Any]) -> dict[str, Any]:
    return {"name": rec["name"], "provider": rec["provider"],
            "provider_label": provider(rec["provider"]).label,
            "server_name": rec["settings"]["server_name"], "status": rec.get("status")}


def connect_info(rec: dict[str, Any]) -> dict[str, Any]:
    s, prov = rec["settings"], provider(rec["provider"])
    host = rec.get("ssh", {}).get("host")
    return {
        "address": rec.get("address"), "port": s["port"],
        "join": f"{rec.get('address')}:{s['port']}",
        "password": rec["secrets"].get("password"),
        "server_name": s["server_name"], "world_name": s["world_name"],
        "public": s["public"], "crossplay": s["crossplay"],
        "ports": f"UDP {s['port']}-{s['port'] + 1}",
        "host": prov.label,
        "ssh": f"ssh -p {rec['ssh'].get('port', 22)} {ssh.target(rec)}" if host else None,
        "notes": prov.network_notes(rec),
        "cost": prov.describe_cost(rec),
    }


def make_handler(token: str, port: int, agent: agent_client.AgentClient) -> type[BaseHTTPRequestHandler]:
    sessions: set[str] = set()
    lock = threading.Lock()
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "valheim-dashboard"
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:
            pass

        def _sid(self) -> str | None:
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie") or "")
            except Exception:
                return None
            morsel = cookie.get("vhsession")
            return morsel.value if morsel else None

        def _authed(self) -> bool:
            with lock:
                return self._sid() in sessions

        def _new_session_cookie(self) -> str:
            sid = secrets.token_urlsafe(32)
            with lock:
                sessions.add(sid)
            return f"vhsession={sid}; HttpOnly; SameSite=Strict; Path=/"

        def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                        "script-src 'self' 'unsafe-inline'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: Any, cookie: str | None = None) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json",
                       {"Set-Cookie": cookie} if cookie else None)

        def _host_ok(self) -> bool:
            if self.headers.get("Host") in allowed_hosts:
                return True
            self._json(HTTPStatus.FORBIDDEN, {"error": "bad host"})
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

        def _proxy(self, rec: dict[str, Any], path: str, body: dict | None = None) -> None:
            try:
                status, payload = agent.call(rec, path, body)
                self._json(status, payload)
            except agent_client.AgentError as exc:
                self._json(HTTPStatus.BAD_GATEWAY, {"offline": True, "error": str(exc)})

        def _server(self, name: str) -> dict[str, Any] | None:
            if not registry.path_for(name).exists():
                self._json(HTTPStatus.NOT_FOUND, {"error": "no such server"})
                return None
            return registry.load(name)

        def do_GET(self) -> None:  # noqa: N802
            if not self._host_ok():
                return
            route = urlparse(self.path)
            path = route.path

            if path == "/":
                supplied = parse_qs(route.query).get("login", [""])[0]
                if supplied and hmac.compare_digest(supplied, token):
                    # Redirect so the token does not stay in the address bar.
                    self._send(HTTPStatus.FOUND, b"", "text/plain",
                               {"Location": "/", "Set-Cookie": self._new_session_cookie()})
                    return
                page = "index.html" if self._authed() else "login.html"
                self._send(HTTPStatus.OK, (STATIC_DIR / page).read_bytes(), "text/html; charset=utf-8")
                return

            if path.startswith("/static/"):
                name = path[len("/static/"):]
                if name not in STATIC_ASSETS or (name not in PUBLIC_ASSETS and not self._authed()):
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                ctype = "text/css" if name.endswith(".css") else "application/javascript"
                self._send(HTTPStatus.OK, (STATIC_DIR / name).read_bytes(), ctype)
                return

            if not self._authed():
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "sign in required"})
                return

            if path == "/api/servers":
                self._json(HTTPStatus.OK, {"servers": [summary(r) for r in registry.all_servers()]})
                return

            m = SERVER_ROUTE.match(path)
            if not m:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            rec = self._server(m.group(1))
            if rec is None:
                return
            what = m.group(2)
            if what == "connect":
                self._json(HTTPStatus.OK, connect_info(rec))
            elif what == "reach":
                self._json(HTTPStatus.OK, host_a2s.query(rec.get("address") or "", rec["settings"]["port"] + 1))
            elif what in ("status", "backups"):
                self._proxy(rec, f"/api/{what}")
            elif what == "logs":
                self._proxy(rec, "/api/logs?n=200")
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._host_ok():
                return
            path = urlparse(self.path).path
            body = self._body()

            if path == "/api/login":
                if hmac.compare_digest(str(body.get("token", "")), token):
                    self._json(HTTPStatus.OK, {"ok": True}, cookie=self._new_session_cookie())
                else:
                    self._json(HTTPStatus.UNAUTHORIZED, {"error": "bad token"})
                return

            if not self._authed():
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "sign in required"})
                return

            if path == "/api/logout":
                with lock:
                    sessions.discard(self._sid() or "")
                self._json(HTTPStatus.OK, {"ok": True}, cookie="vhsession=; Max-Age=0; Path=/")
                return

            m = SERVER_ROUTE.match(path)
            if m and m.group(2) in ("action", "list"):
                rec = self._server(m.group(1))
                if rec is not None:
                    self._proxy(rec, f"/api/{m.group(2)}", body)
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    return Handler


def serve(port: int, open_browser: bool = True) -> int:
    token = dashboard_token()
    tunnels = ssh.Tunnels()
    agent = agent_client.AgentClient(tunnels)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(token, port, agent))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{port}/?login={token}"
    print(f"Dashboard: {url}\nKeep this running while you use it. Ctrl-C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        tunnels.close()
    return 0
