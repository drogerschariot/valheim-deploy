"""The `valheim` command."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any

from . import __version__, agent_client, paths, registry, settings, ssh
from .providers import get as provider, my_public_ip
from .ui import UI

HOST_CHOICES = ["hetzner", "aws", "existing", "local"]
READY_TIMEOUT = 25 * 60  # a first start downloads the game, then builds a world
AGENT_SILENCE_LIMIT = 180

HOME_NOTICE = """\
This is an ADVANCED setup. The server runs on this PC, and for friends outside
your home to join you need to be comfortable with:

  * Your router: signing in to it, and forwarding UDP ports to this PC
  * Firewalls: allowing the game through Windows Firewall
  * Public IP addresses: yours, whether it changes, and CGNAT, where your
    internet provider shares one address between customers and no port
    forward can work

The Network tab walks you through each of these and can do most of it for you,
but routers differ and some need changing by hand. If that sounds like a lot, a
paid Valheim host will be a much better experience.
"""

ADVANCED_NOTICE = """\
This is an ADVANCED setup. You are about to run a real server, and you are
responsible for keeping it reachable, patched and paid for. You should be
comfortable with:

  * Firewalls: what an inbound rule is, and how to open UDP ports
  * Public IP addresses: yours, and whether it changes (or is behind CGNAT)
  * Port forwarding on a router, if the server is at home
  * SSH: logging in to a Linux machine with a key
  * Cloud billing: a cloud server costs money until you delete it

If those are new to you, a paid Valheim host will be a much better experience.
"""


def client() -> agent_client.AgentClient:
    return agent_client.AgentClient(ssh.Tunnels())


def next_port(rec: dict[str, Any]) -> int:
    """First free base port on this machine. Only servers sharing a machine can
    collide; a new cloud VM is empty, so it always gets the default."""
    if rec["provider"] in ("local", "windows"):
        same = lambda r: r["provider"] == rec["provider"]  # noqa: E731
    elif rec["provider"] == "existing":
        same = lambda r: r.get("ssh", {}).get("host") == rec["ssh"]["host"]  # noqa: E731
    else:
        return settings.DEFAULT_PORT
    used = {r["settings"]["port"] for r in registry.all_servers() if same(r)}
    port = settings.DEFAULT_PORT
    while port in used:
        port += settings.PORT_STEP
    return port


def collect_settings(ui: UI, rec: dict[str, Any]) -> None:
    s = rec["settings"]
    ui.heading("Your server")
    s["server_name"] = ui.ask("server_name", "Server name (shown to players)", rec["name"])
    s["world_name"] = ui.ask("world_name", "World name", "Dedicated")
    generated = secrets.token_urlsafe(9)
    rec["secrets"]["password"] = ui.ask(
        "password", "Join password (Enter to use the generated one)", generated,
        check=lambda v: settings.check_password(v, s["server_name"]))
    s["public"] = ui.confirm(
        "public", "List it in the public community browser? Anyone can then find it; "
                  "the password is the only thing keeping them out.", False)
    s["crossplay"] = ui.confirm(
        "crossplay", "Allow Xbox and Game Pass players (crossplay)? Players join with a code.", False)

    preset = ui.choose("preset", "World difficulty?",
                       [("", "Valheim default")] + [(p, p) for p in settings.PRESETS])
    s["preset"] = preset

    raw = ui.ask("admins", "Admin Steam IDs, comma separated (- for none; e.g. Steam_76561198000000000)", "-")
    s["admins"] = [a.strip() for a in raw.split(",") if a.strip() and a.strip() != "-"]
    s["backup_time"] = ui.ask("backup_time", "Daily backup time on the server, 24-hour HH:MM", "05:30")


def review(ui: UI, rec: dict[str, Any], prov: Any) -> None:
    s = rec["settings"]
    ui.heading("Review")
    ui.say(f"  Host:       {prov.label}")
    ui.say(f"  Server:     {s['server_name']}  (world: {s['world_name']})")
    ui.say(f"  Ports:      UDP {s['port']}-{s['port'] + 1}")
    ui.say(f"  Listed:     {'yes, in the community browser' if s['public'] else 'no'}")
    ui.say(f"  Crossplay:  {'yes' if s['crossplay'] else 'no'}")
    ui.say(f"  Difficulty: {s['preset'] or 'default'}")
    ui.say(f"  Cost:       {prov.describe_cost(rec)}")


def wait_ready(ui: UI, rec: dict[str, Any]) -> bool:
    ui.say("\nWaiting for the world to load (a first start can take several minutes)...")
    agent, deadline, last = client(), time.time() + READY_TIMEOUT, ""
    heard_from = time.time()
    try:
        while time.time() < deadline:
            try:
                status, body = agent.call(rec, "/api/status", timeout=30)
                heard_from = time.time()
                service = body.get("service", {}) if status == 200 else {}
                if service.get("ready"):
                    return True
                phase = service.get("sub", "starting")
                if phase != last:
                    ui.say(f"  {phase}")
                    last = phase
            except agent_client.AgentError as exc:
                # A silent agent will not recover by itself, so say so now
                # rather than sitting out the whole timeout.
                if time.time() - heard_from > AGENT_SILENCE_LIMIT:
                    ui.warn(f"  The server's agent is not answering: {exc}")
                    ui.warn(f"  On the host, see: journalctl -u valheim-agent-{rec['name']} -n 30")
                    return False
            time.sleep(5)
        return False
    finally:
        agent.tunnels.close()


def create_server(ui: UI) -> int:
    """The whole creation flow. The terminal drives this with prompts, the
    browser with a filled-in answers dict, so there is one set of rules."""
    ui.heading("Valheim server setup")
    ui.warn(ADVANCED_NOTICE)
    if not ui.confirm("understood", "I understand and want to continue", False):
        ui.say("Stopped. Nothing was created.")
        return 1

    key = ui.choose("host", "Where should this server run?",
                    [(k, provider(k).label) for k in HOST_CHOICES])
    prov = provider(key)
    problem = prov.preflight()
    if problem:
        raise SystemExit(problem)
    name = ui.ask("name", "Short name for this server (lowercase, used in commands)", "",
                  check=settings.check_slug)
    if registry.path_for(name).exists():
        raise SystemExit(f"There is already a server called {name}.")
    players = int(ui.ask("players", "How many players at once?", "4"))

    rec: dict[str, Any] = {
        "name": name, "provider": key, "players": players, "status": "planned",
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "address": None, "ssh": {}, "resources": {},
        "settings": json.loads(json.dumps(settings.DEFAULTS)),
        "secrets": {"agent_token": secrets.token_urlsafe(32)},
    }
    ui.heading(prov.label)
    prov.collect(ui, rec)
    rec["settings"]["port"] = next_port(rec)
    collect_settings(ui, rec)
    try:
        settings.validate(rec["settings"])
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    review(ui, rec, prov)
    if not ui.confirm("go", "\nCreate this server?", False):
        ui.say("Stopped. Nothing was created.")
        return 1

    # From here on the record exists, so an interruption leaves something
    # `destroy` can clean up.
    registry.save(rec)
    prov.create(ui, rec)
    registry.save(rec)
    finish_install(ui, rec)
    return 0


def finish_install(ui: UI, rec: dict[str, Any]) -> None:
    provider(rec["provider"]).install(ui, rec)
    rec["status"] = "installed"
    registry.save(rec)
    if provider(rec["provider"]).waits_itself:
        pass
    elif wait_ready(ui, rec):
        ui.ok("\nThe server is up.")
    else:
        ui.warn("\nThe server has not reported ready yet. It may still be loading; check its logs "
                "on the dashboard.")
    if ui.show_info:
        print_info(ui, rec)


def destroy_server(ui: UI, rec: dict[str, Any]) -> None:
    provider(rec["provider"]).destroy(ui, rec)
    registry.remove(rec["name"])
    ui.ok(f"{rec['name']} is gone.")


def allow_current_ip(ui: UI, rec: dict[str, Any]) -> None:
    ip = my_public_ip()
    provider(rec["provider"]).allow_ip(ui, rec, ip)
    ui.ok(f"SSH is now allowed from {ip} only.")


def cmd_new(args: argparse.Namespace) -> int:
    answers = json.load(open(args.answers, encoding="utf-8")) if args.answers else None
    return create_server(UI(answers))


def print_info(ui: UI, rec: dict[str, Any]) -> None:
    s, prov = rec["settings"], provider(rec["provider"])
    address = rec.get("address") or "(not known yet)"
    ui.heading(f"{rec['name']}: everything you need")
    ui.say(f"  Join address:  {address}:{s['port']}")
    ui.say(f"  Password:      {rec['secrets']['password']}")
    ui.say(f"  Server name:   {s['server_name']}")
    ui.say(f"  World:         {s['world_name']}")
    ui.say(f"  Game ports:    UDP {s['port']}-{s['port'] + 1}")
    ui.say(f"  Hosted on:     {prov.label}")
    if rec.get("ssh", {}).get("host"):
        key = f"-i {rec['ssh']['key']} " if rec["ssh"].get("key") else ""
        ui.say(f"  SSH:           ssh {key}-p {rec['ssh'].get('port', 22)} {ssh.target(rec)}")
    ui.say(f"  Dashboard:     {paths.command()} dashboard")
    ui.say("\n  To join: in Valheim choose Start Game > Join Game > Add server, and enter the join address.")
    if s["crossplay"]:
        ui.say("  Crossplay servers also show a join code in the dashboard's Connect tab.")
    notes = prov.network_notes(rec)
    if notes:
        ui.heading("You still need to do this before players can connect")
        for note in notes:
            ui.say(f"  [ ] {note}")
    ui.say(f"\n  Check it from outside: {paths.command()} doctor {rec['name']}")
    ui.say(f"  {prov.describe_cost(rec)}")


def cmd_list(args: argparse.Namespace) -> int:
    servers = registry.all_servers()
    if not servers:
        print(f"No servers yet. Create one with: {paths.command()} new")
        return 0
    print(f"{'NAME':<20}{'HOST':<12}{'JOIN ADDRESS':<28}STATUS")
    for r in servers:
        join = f"{r.get('address') or '-'}:{r['settings']['port']}"
        print(f"{r['name']:<20}{r['provider']:<12}{join:<28}{r.get('status', '?')}")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    print_info(UI(), registry.load(args.name))
    return 0


def cmd_action(args: argparse.Namespace) -> int:
    rec, agent = registry.load(args.name), client()
    try:
        status, body = agent.call(rec, "/api/action", {"action": args.command.replace("-", "_"), "confirm": True})
        print(body.get("detail") or body.get("error") or status)
        return 0 if status == 200 else 1
    except agent_client.AgentError as exc:
        print(f"Could not reach the server: {exc}", file=sys.stderr)
        return 1
    finally:
        agent.tunnels.close()


def cmd_update(args: argparse.Namespace) -> int:
    rec = registry.load(args.name)
    return provider(rec["provider"]).update(UI(), rec)


def coerce(raw: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def cmd_set(args: argparse.Namespace) -> int:
    rec, ui = registry.load(args.name), UI()
    for pair in args.pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"Expected key=value, got {pair!r}.")
        if key == "password":
            rec["secrets"]["password"] = settings.check_password(value, rec["settings"]["server_name"])
        elif key == "port":
            raise SystemExit("The port cannot be changed after creation: firewalls and forwards depend on it.")
        elif key in ("server_name", "world_name"):
            rec["settings"][key] = value
        else:
            rec["settings"][key] = coerce(value)
    try:
        settings.validate(rec["settings"])
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if any(p.startswith("world_name=") for p in args.pairs):
        ui.warn("Changing world_name starts a NEW world. The old world stays on disk but is no longer loaded.")
        if not ui.confirm("x", "Continue?", False):
            return 1
    settings.check_password(rec["secrets"]["password"], rec["settings"]["server_name"])
    registry.save(rec)
    return cmd_apply(args)


def cmd_apply(args: argparse.Namespace) -> int:
    rec = registry.load(args.name)
    provider(rec["provider"]).install(UI(), rec)
    rec["status"] = "installed"
    registry.save(rec)
    print("Applied. If settings changed, the server restarted (it saves first).")
    return 0


def cmd_backups(args: argparse.Namespace) -> int:
    rec = registry.load(args.name)
    code = provider(rec["provider"]).fetch_backups(UI(), rec)
    print(f"Backups copied to {registry.home() / 'backups' / rec['name']}" if code == 0
          else "Could not copy the backups.")
    return code


def cmd_allow_my_ip(args: argparse.Namespace) -> int:
    allow_current_ip(UI(), registry.load(args.name))
    return 0


def doctor_checks(rec: dict[str, Any]) -> list[tuple[str, bool, str]]:
    """(check, passed, what to do about it)."""
    from . import host_a2s
    checks: list[tuple[str, bool, str]] = []
    port = rec["settings"]["port"]
    if rec["provider"] != "local":
        ok = ssh.reachable(rec)
        hint = f"Run {paths.command()} allow-my-ip {rec['name']} if your IP changed." if rec["provider"] in ("aws", "hetzner") \
            else "Check the host is on and your SSH login still works."
        checks.append(("SSH login", ok, "" if ok else hint))
    agent, status = client(), {}
    try:
        code, body = agent.call(rec, "/api/status", timeout=30)
        status = body if code == 200 else {}
        detail = "" if status else f"The agent answered {code}."
        checks.append(("Agent", bool(status), detail))
    except agent_client.AgentError as exc:
        checks.append(("Agent", False, str(exc)))
    finally:
        agent.tunnels.close()
    service = status.get("service", {})
    checks.append(("Game running", service.get("active") == "active",
                   "" if service.get("active") == "active" else f"Start it: {paths.command()} start {rec['name']}"))
    checks.append(("World loaded", bool(service.get("ready")), "" if service.get("ready")
                   else "It may still be loading; check the logs in the dashboard."))
    if rec["settings"]["crossplay"]:
        checks.append(("Reachable from the internet", True,
                       "Not testable: crossplay servers do not answer the query port."))
    else:
        result = host_a2s.query(rec["address"], port + 1)
        hint = ""
        if not result["reachable"]:
            hint = (f"No answer on UDP {port + 1} of {rec['address']}. Check the firewall and port "
                    "forward. From inside your own network this can fail even when outside "
                    "players connect fine, so ask a friend to try.")
        checks.append(("Reachable from the internet", result["reachable"], hint))
    return checks


def cmd_doctor(args: argparse.Namespace) -> int:
    rec, ui = registry.load(args.name), UI()
    failed = False
    for check, passed, hint in doctor_checks(rec):
        print(f"  {'ok  ' if passed else 'FAIL'} {check}" + (f"\n       {hint}" if hint else ""))
        failed |= not passed
    ui.say()
    return 1 if failed else 0


def cmd_destroy(args: argparse.Namespace) -> int:
    rec, ui = registry.load(args.name), UI()
    prov = provider(rec["provider"])
    ui.warn(f"This permanently deletes {rec['name']} ({prov.label}) and its world.")
    if rec.get("status") == "installed" and ui.confirm("x", "Download its backups to this computer first?", True):
        if prov.fetch_backups(ui, rec) != 0:
            ui.warn("The backup download failed.")
            if not ui.confirm("x", "Delete anyway, losing the world?", False):
                return 1
    if input(f"Type {rec['name']} to confirm: ").strip() != rec["name"]:
        ui.say("Names did not match. Nothing was deleted.")
        return 1
    destroy_server(ui, rec)
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    from .dashboard import serve
    return serve(args.port, open_browser=not args.no_browser)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="valheim", description="Create and run Valheim dedicated servers.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    def add(name: str, func: Any, help_text: str, named: bool = True) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        if named:
            p.add_argument("name")
        p.set_defaults(func=func)
        return p

    add("new", cmd_new, "create a server (guided)", False).add_argument(
        "--answers", help="JSON file of answers, for unattended use")
    add("list", cmd_list, "list your servers", False)
    add("info", cmd_info, "join address, password and everything else")
    dash = add("dashboard", cmd_dashboard, "open the dashboard for all your servers", False)
    dash.add_argument("--port", type=int, default=8456)
    dash.add_argument("--no-browser", action="store_true")
    add("doctor", cmd_doctor, "check a server is healthy and reachable")
    for action, text in (("start", "start the game"), ("stop", "stop the game (saves first)"),
                         ("restart", "restart the game (saves first)"), ("backup", "take a backup now"),
                         ("update-check", "see whether a game update is available")):
        add(action, cmd_action, text)
    add("update", cmd_update, "apply a game update: backs up, updates, health-checks")
    set_parser = add("set", cmd_set, "change settings, e.g. set myworld preset=Hard")
    set_parser.add_argument("pairs", nargs="+", metavar="key=value")
    add("apply", cmd_apply, "re-run the install, e.g. to retry after a failure")
    add("backups", cmd_backups, "download all backups to this computer")
    add("allow-my-ip", cmd_allow_my_ip, "allow SSH from your current IP (cloud servers)")
    add("destroy", cmd_destroy, "permanently delete a server and stop its billing")
    return parser


def run_default() -> int:
    """Double-clicked with no arguments: open the dashboard. In a packaged app
    the console closes the moment we exit, so hold any error on screen."""
    from .dashboard import serve
    try:
        return serve(8456)
    except (Exception, SystemExit) as exc:
        print(f"Could not start: {exc}")
        if paths.is_frozen():
            input("Press Enter to close.")
        return 1


def main() -> int:
    if os.name == "nt":
        os.system("")  # turns on colour handling in the Windows console
    args = build_parser().parse_args()
    try:
        if not args.command:
            return run_default()
        return args.func(args)
    except KeyboardInterrupt:
        print(f"\nInterrupted. If a cloud server was being created, `{paths.command()} list` shows it, "
              f"and `{paths.command()} destroy NAME` removes it.")
        return 130
