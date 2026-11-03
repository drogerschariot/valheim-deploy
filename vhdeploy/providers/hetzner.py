"""Hetzner Cloud: the "other" option, recommended for most people.

Chosen over AWS for this workload because a game server runs flat out around
the clock, which is the case on-demand pricing is worst at, and it needs no
aws-cli or IAM setup: one API token. Plans and prices come from the live API at
wizard time, because they change and a number written here would go stale.

ARM plans are filtered out. The Valheim server is x86-64 only.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .. import registry, ssh
from . import Provider, my_public_ip, sizing

TOKEN_URL = "https://console.hetzner.cloud (project > Security > API tokens, Read & Write)"


def api_base() -> str:
    return os.environ.get("HCLOUD_API", "https://api.hetzner.cloud/v1")


def token(ui: Any = None) -> str:
    value = os.environ.get("HCLOUD_TOKEN") or registry.credential("hcloud_token")
    if value:
        return value
    if ui is None:
        raise SystemExit("No Hetzner API token. Set HCLOUD_TOKEN.")
    ui.say(f"Create an API token at {TOKEN_URL}.")
    value = ui.secret("hcloud_token", "Hetzner API token")
    if not value:
        raise SystemExit("A token is required.")
    registry.store_credential("hcloud_token", value)
    return value


def api(method: str, path: str, body: Any = None, tok: str | None = None) -> Any:
    request = urllib.request.Request(
        f"{api_base()}{path}", method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {tok or token()}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as res:
            raw = res.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        try:
            detail = json.loads(detail)["error"]["message"]
        except (ValueError, KeyError):
            pass
        raise SystemExit(f"Hetzner refused {method} {path}: {detail}") from exc


def save_token(value: str) -> None:
    """Keep a token only after Hetzner has accepted it."""
    api("GET", "/locations", tok=value)
    registry.store_credential("hcloud_token", value)


def plans(location: str, min_gb: int) -> list[dict[str, Any]]:
    """x86 plans offered at this location, with RAM >= min_gb, cheapest first."""
    out = []
    for t in api("GET", "/server_types?per_page=50")["server_types"]:
        price = next((p for p in t.get("prices", []) if p["location"] == location), None)
        if t["architecture"] != "x86" or not price or t["memory"] < min_gb or t["disk"] < 30:
            continue
        if t.get("deprecated") or t.get("deprecation"):
            continue
        out.append({"name": t["name"], "cores": t["cores"], "memory": t["memory"], "disk": t["disk"],
                     "cpu_type": t["cpu_type"], "monthly": float(price["price_monthly"]["gross"])})
    return sorted(out, key=lambda p: (p["monthly"], p["name"]))


def rules(ssh_cidr: str, port: int) -> list[dict[str, Any]]:
    return [
        {"direction": "in", "protocol": "tcp", "port": "22", "source_ips": [ssh_cidr],
         "description": "SSH, owner only"},
        {"direction": "in", "protocol": "udp", "port": f"{port}-{port + 1}",
         "source_ips": ["0.0.0.0/0", "::/0"], "description": "Valheim"},
    ]


class Hetzner(Provider):
    key = "hetzner"
    label = "Hetzner Cloud (recommended: cheapest always-on option)"

    def wizard_options(self, params: dict[str, str]) -> dict[str, Any]:
        if not (os.environ.get("HCLOUD_TOKEN") or registry.credential("hcloud_token")):
            return {"needs_token": True, "token_help": TOKEN_URL}
        locations = api("GET", "/locations")["locations"]
        location = params.get("location") or locations[0]["name"]
        want = sizing(int(params.get("players") or 4))
        return {
            "needs_token": False, "recommended_gb": want, "location": location,
            "locations": [{"value": l["name"], "label": f"{l['city']}, {l['country']} ({l['name']})"}
                          for l in locations],
            "plans": [{"value": p["name"],
                       "label": f"{p['name']}: {p['cores']} {p['cpu_type']} vCPU, {p['memory']:g} GB RAM, "
                                f"{p['disk']} GB disk, EUR {p['monthly']:.2f}/month"}
                      for p in plans(location, want)[:6]],
        }

    def collect(self, ui: Any, rec: dict[str, Any]) -> None:
        token(ui)
        locations = api("GET", "/locations")["locations"]
        location = ui.choose("hcloud_location", "Which location? Pick the closest to your players.",
                             [(l["name"], f"{l['city']}, {l['country']} ({l['name']})") for l in locations])
        want = sizing(rec["players"])
        options = plans(location, want)
        if not options:
            raise SystemExit(f"No x86 plan with {want} GB+ is offered at {location} right now. "
                             "Try another location.")
        plan = ui.choose("hcloud_plan", f"Which plan? {want} GB+ recommended; prices are live from Hetzner.",
                         [(p["name"], f"{p['name']}: {p['cores']} {p['cpu_type']} vCPU, {p['memory']:g} GB RAM, "
                                      f"{p['disk']} GB disk, EUR {p['monthly']:.2f}/month")
                          for p in options[:6]])
        chosen = next(p for p in options if p["name"] == plan)
        rec["resources"] = {"location": location, "plan": plan, "monthly": chosen["monthly"]}

    def describe_cost(self, rec: dict[str, Any]) -> str:
        return f"Hetzner bills about EUR {rec['resources']['monthly']:.2f}/month until the server is deleted."

    def create(self, ui: Any, rec: dict[str, Any]) -> None:
        res, name = rec["resources"], rec["name"]
        key = ssh.generate_key(name)
        public = Path(key + ".pub").read_text().strip()
        labels = {"managed-by": "valheim-deploy", "valheim-server": name}

        ui.say("Creating SSH key, firewall and server at Hetzner...")
        res["ssh_key_id"] = api("POST", "/ssh_keys", {"name": f"valheim-{name}", "public_key": public,
                                                      "labels": labels})["ssh_key"]["id"]
        registry.save(rec)
        cidr = f"{my_public_ip()}/32"
        res["firewall_id"] = api("POST", "/firewalls", {
            "name": f"valheim-{name}", "labels": labels,
            "rules": rules(cidr, rec["settings"]["port"])})["firewall"]["id"]
        registry.save(rec)
        server = api("POST", "/servers", {
            "name": f"valheim-{name}", "server_type": res["plan"], "image": "ubuntu-24.04",
            "location": res["location"], "ssh_keys": [res["ssh_key_id"]],
            "firewalls": [{"firewall": res["firewall_id"]}], "labels": labels,
        })["server"]
        res["server_id"] = server["id"]
        registry.save(rec)

        deadline = time.time() + 300
        while server["status"] != "running":
            if time.time() > deadline:
                raise SystemExit("The Hetzner server did not start within 5 minutes.")
            time.sleep(3)
            server = api("GET", f"/servers/{res['server_id']}")["server"]
        rec["address"] = server["public_net"]["ipv4"]["ip"]
        rec["ssh"] = {"host": rec["address"], "user": "root", "port": 22, "key": key}
        registry.save(rec)

    def allow_ip(self, ui: Any, rec: dict[str, Any], ip: str) -> None:
        res = rec["resources"]
        api("POST", f"/firewalls/{res['firewall_id']}/actions/set_rules",
            {"rules": rules(f"{ip}/32", rec["settings"]["port"])})

    def destroy(self, ui: Any, rec: dict[str, Any]) -> None:
        res = rec.get("resources", {})
        if res.get("server_id"):
            ui.say("Deleting the server...")
            api("DELETE", f"/servers/{res['server_id']}")
        if res.get("firewall_id"):
            # A firewall stays attached, and undeletable, for a moment after
            # its server goes.
            for attempt in range(30):
                try:
                    api("DELETE", f"/firewalls/{res['firewall_id']}")
                    break
                except SystemExit:
                    if attempt == 29:
                        raise
                    time.sleep(3)
        if res.get("ssh_key_id"):
            api("DELETE", f"/ssh_keys/{res['ssh_key_id']}")
