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
import os
import re
import secrets
import threading
import urllib.request
import webbrowser
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import __version__, agent_client, cli, host_a2s, paths, registry, settings, ssh
from .jobs import Busy, Job, Jobs
from .providers import get as provider
from .providers.existing import login_problem, make_key

STATIC_DIR = paths.static_dir()
# A fixed set, not a filename pattern. Making something readable before sign-in
# should take a code change.
PUBLIC_ASSETS = {"login.css"}
STATIC_ASSETS = {"app.js", "wizard.js", "app.css", "login.css"}
SERVER_ROUTE = re.compile(r"^/api/servers/([a-z0-9-]+)/(status|logs|backups|connect|action|list|reach|network|autostart)$")
# Set by the Windows app: what the tray's Quit does, so the page can offer it too.
QUIT: Any = None
JOB_ROUTE = re.compile(r"^/api/servers/([a-z0-9-]+)/(update|apply|download-backups|allow-my-ip|destroy)$")


def dashboard_token() -> str:
    path = registry.home() / "dashboard.token"
    if not path.exists():
        registry.write_private(path, secrets.token_urlsafe(24))
    return path.read_text().strip()


def summary(rec: dict[str, Any]) -> dict[str, Any]:
    return {"name": rec["name"], "provider": rec["provider"],
            "provider_label": provider(rec["provider"]).label,
            "server_name": rec["settings"]["server_name"], "status": rec.get("status"),
            "cloud": rec["provider"] in ("aws", "hetzner"),
            "this_pc": rec["provider"] == "windows",
            "autostart": rec["settings"].get("autostart", True),
            # A cloud machine that was never created cannot be retried into
            # existence, only removed.
            "can_retry": rec.get("status") != "installed"
            and (rec["provider"] == "local" or bool(rec.get("ssh", {}).get("host")))}


def wizard_options(host: str | None, params: dict[str, str]) -> dict[str, Any]:
    """Everything the browser wizard needs to draw itself. With no host, the
    list of hosts and what is missing for each; with one, that host's choices."""
    if host:
        return provider(host).wizard_options(params)
    hosts = []
    for key in cli.HOST_CHOICES:
        prov = provider(key)
        hosts.append({"key": key, "label": prov.label, "problem": prov.preflight(),
                      "warnings": prov.warnings(), "needs_network_setup": prov.needs_network_setup})
    return {"hosts": hosts, "notice": cli.ADVANCED_NOTICE, "presets": settings.PRESETS,
            "suggested_password": secrets.token_urlsafe(9)}


def create_job(jobs: Jobs, body: dict[str, Any]) -> Job:
    """Empty fields mean "use the default", exactly as Enter does in a terminal.
    The warning is enforced by create_server, which reads `understood`."""
    answers = {k: v for k, v in body.items() if v not in ("", None)}
    try:
        name = settings.check_slug(str(answers.get("name", "")))
    except ValueError as exc:
        raise Refused(f"Server name: {exc}.") from exc
    if registry.path_for(name).exists():
        raise Refused(f"There is already a server called {name}.")
    return jobs.start(name, "create", cli.create_server, answers)


def server_job(jobs: Jobs, rec: dict[str, Any], what: str, body: dict[str, Any]) -> Job:
    name, prov = rec["name"], provider(rec["provider"])

    def apply(ui: Any) -> None:
        cli.finish_install(ui, rec)

    def update(ui: Any) -> int:
        return prov.update(ui, rec)

    def download(ui: Any) -> int:
        code = prov.fetch_backups(ui, rec)
        if code == 0:
            ui.ok(f"Backups copied to {registry.home() / 'backups' / name}")
        return code

    def allow(ui: Any) -> None:
        cli.allow_current_ip(ui, rec)

    def destroy(ui: Any) -> int:
        if body.get("download") and rec.get("status") == "installed":
            ui.say("Downloading backups first...")
            if download(ui) != 0 and not body.get("force"):
                raise SystemExit("The backup download failed, so nothing was deleted.")
        cli.destroy_server(ui, rec)
        return 0

    if what == "destroy" and body.get("confirm_name") != name:
        raise Refused("Type the server's name to confirm.")
    if what == "allow-my-ip" and rec["provider"] not in ("aws", "hetzner"):
        raise Refused("This only applies to cloud servers.")
    work = {"apply": apply, "update": update, "download-backups": download,
            "allow-my-ip": allow, "destroy": destroy}[what]
    return jobs.start(name, what, work)


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


def local_pc_action(rec: dict[str, Any], what: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Network fixes and the auto-start switch, for servers on this PC."""
    if rec["provider"] != "windows":
        return HTTPStatus.CONFLICT, {"error": "This only applies to servers on this PC."}
    if what == "autostart":
        rec["settings"]["autostart"] = bool(body.get("on"))
        registry.save(rec)
        return HTTPStatus.OK, {"ok": True, "autostart": rec["settings"]["autostart"]}
    from . import winhost
    from .winhost import network
    action, port = str(body.get("action", "")), rec["settings"]["port"]
    if action == "open-folder":
        folder = winhost.instance_dir(rec["name"]) / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        if not hasattr(os, "startfile"):
            return HTTPStatus.CONFLICT, {"error": f"Backups are in {folder}"}
        os.startfile(folder)  # noqa: S606  (Explorer, on the user's own PC)
        return HTTPStatus.OK, {"ok": True}
    try:
        if action == "open-ports":
            return HTTPStatus.OK, {"ok": True, "detail": network.open_ports(rec)}
        if action == "close-ports":
            network.close_ports(rec)
            registry.save(rec)
            return HTTPStatus.OK, {"ok": True, "detail": "The port forward was removed."}
        if action in ("firewall-add", "firewall-remove"):
            verb = action.split("-")[1]
            if not network.elevate_firewall(verb, rec["name"], port):
                return HTTPStatus.CONFLICT, {"error": "Windows did not get permission, so nothing changed. "
                                                      "Choose Yes when Windows asks."}
            done = "added" if verb == "add" else "removed"
            return HTTPStatus.OK, {"ok": True, "detail": f"The firewall rule was {done}."}
    except SystemExit as exc:
        return HTTPStatus.CONFLICT, {"error": str(exc.code)}
    return HTTPStatus.BAD_REQUEST, {"error": "unknown action"}


class Refused(Exception):
    """A request that makes sense but cannot be done, with a reason to show."""


def make_handler(token: str, port: int, agent: agent_client.AgentClient,
                 jobs: Jobs | None = None) -> type[BaseHTTPRequestHandler]:
    jobs = jobs or Jobs()
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
                self._json(HTTPStatus.OK, {"app_mode": QUIT is not None, "servers": [
                    {**summary(r), "job": getattr(jobs.running(r["name"]), "id", None)}
                    for r in registry.all_servers()]})
                return

            if path == "/api/wizard/options":
                query = {k: v[0] for k, v in parse_qs(route.query).items()}
                host = query.pop("host", None)
                if host is not None and host not in cli.HOST_CHOICES:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "unknown host"})
                    return
                try:
                    self._json(HTTPStatus.OK, wizard_options(host, query))
                except (SystemExit, OSError) as exc:
                    self._json(HTTPStatus.BAD_GATEWAY, {"error": str(getattr(exc, "code", exc))})
                return

            if path.startswith("/api/jobs/"):
                job = jobs.get(path[len("/api/jobs/"):])
                if job:
                    self._json(HTTPStatus.OK, job.view())
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "no such job"})
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
            elif what == "network":
                from .winhost import network
                info = network.status(rec, refresh="refresh" in route.query)
                network.remember_public_ip(rec, info["public_ip"])
                self._json(HTTPStatus.OK, info)
            elif what in ("status", "backups"):
                self._proxy(rec, f"/api/{what}")
            elif what == "logs":
                self._proxy(rec, "/api/logs?n=200")
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._host_ok():
                return
            # A plain cross-site form cannot set this type, so this is a second
            # lock on these routes behind the SameSite cookie.
            if self.headers.get_content_type() != "application/json":
                self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "JSON required"})
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

            if path == "/api/wizard/connect":
                # Credentials go to the provider to be checked, then to the
                # credentials file. Never into a reply or a log.
                host = str(body.get("host", ""))
                prov = provider(host) if host in cli.HOST_CHOICES else None
                if not prov or not prov.guide:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "That host has no account to connect."})
                    return
                fields = {f["key"]: str(body.get(f["key"], "")) for f in prov.guide["fields"]}
                try:
                    self._json(HTTPStatus.OK, {"connected": prov.connect(fields)})
                except (SystemExit, OSError) as exc:
                    reason = exc.code if isinstance(exc, SystemExit) else "Could not reach the provider."
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(reason)})
                return

            if path == "/api/wizard/ssh-key":
                self._json(HTTPStatus.OK, make_key())
                return

            if path == "/api/wizard/check-ssh":
                rec = {"name": "wizard-check", "ssh": {
                    "host": str(body.get("ssh_host", "")), "user": str(body.get("ssh_user") or "root"),
                    "port": int(body.get("ssh_port") or 22),
                    "key": "" if body.get("ssh_key") in (None, "", "-") else str(body["ssh_key"])}}
                self._json(HTTPStatus.OK, {"problem": login_problem(rec)})
                return

            job = None
            try:
                if path == "/api/servers":
                    job = create_job(jobs, body)
                else:
                    m = JOB_ROUTE.match(path)
                    if m and registry.path_for(m.group(1)).exists():
                        job = server_job(jobs, registry.load(m.group(1)), m.group(2), body)
            except (Busy, Refused) as exc:
                self._json(HTTPStatus.CONFLICT, {"error": str(exc)})
                return
            if job:
                self._json(HTTPStatus.ACCEPTED, {"job": job.id})
                return

            if path == "/api/app/quit" and QUIT:
                self._json(HTTPStatus.OK, {"ok": True})
                threading.Thread(target=QUIT, daemon=True).start()
                return

            m = SERVER_ROUTE.match(path)
            if m and m.group(2) in ("network", "autostart"):
                rec = self._server(m.group(1))
                if rec is not None:
                    self._json(*local_pc_action(rec, m.group(2), body))
                return
            if m and m.group(2) in ("action", "list"):
                rec = self._server(m.group(1))
                if rec is not None:
                    self._proxy(rec, f"/api/{m.group(2)}", body)
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    return Handler


def already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/static/login.css", timeout=3) as res:
            return (res.headers.get("Server") or "").startswith("valheim-dashboard")
    except OSError:
        return False


def serve(port: int, open_browser: bool = True) -> int:
    token = dashboard_token()
    url = f"http://127.0.0.1:{port}/?login={token}"
    tunnels = ssh.Tunnels()
    agent = agent_client.AgentClient(tunnels)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(token, port, agent))
    except OSError as exc:
        if not already_running(port):
            raise SystemExit(f"Port {port} is in use by another program ({exc}).") from exc
        # Double-clicked a second time: the page is what they wanted.
        print(f"Already running: {url}")
        if open_browser:
            webbrowser.open(url)
        return 0
    server.daemon_threads = True
    print(f"Valheim Deploy {__version__}\n\nDashboard: {url}\n\n"
          "Keep this window open while you use the page, and while a server is being\n"
          "created. Closing it does not stop your game servers.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        tunnels.close()
    return 0
