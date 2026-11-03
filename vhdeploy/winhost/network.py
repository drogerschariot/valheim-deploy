"""Everything about getting friends through to a server on this PC: IP
addresses, the router, CGNAT, UPnP port forwarding and Windows Firewall.

All standard library. Each check answers "unknown" rather than raising, since
home networks vary endlessly and the page must still render.
"""

from __future__ import annotations

import ctypes
import ipaddress
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any
from xml.sax.saxutils import escape

from .. import registry
from . import IS_WINDOWS, game_dir

SSDP_ADDR = ("239.255.255.250", 1900)
SEARCH_TARGETS = ["urn:schemas-upnp-org:device:InternetGatewayDevice:1",
                  "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
                  "urn:schemas-upnp-org:service:WANIPConnection:1"]
WAN_SERVICES = ("WANIPConnection", "WANPPPConnection")
CGNAT = ipaddress.ip_network("100.64.0.0/10")
CREATE_NO_WINDOW = 0x08000000


def run(argv: list[str], timeout: int = 15) -> tuple[int, str]:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False,
                              errors="replace", creationflags=CREATE_NO_WINDOW if IS_WINDOWS else 0)
        return done.returncode, done.stdout + done.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 255, str(exc)


# -- addresses ---------------------------------------------------------------

def lan_ip() -> str | None:
    """This PC's address on the home network. Connecting a UDP socket sends
    nothing; it only makes the OS pick the interface it would route through."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


def parse_route_print(text: str) -> str | None:
    """The default gateway from `route print -4 0.0.0.0`, lowest metric first."""
    best: tuple[int, str] | None = None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0" and parts[-1].isdigit():
            try:
                ipaddress.ip_address(parts[2])
            except ValueError:
                continue  # "On-link"
            if best is None or int(parts[-1]) < best[0]:
                best = (int(parts[-1]), parts[2])
    return best[1] if best else None


def gateway() -> str | None:
    if not IS_WINDOWS:
        return None
    code, out = run(["route", "print", "-4", "0.0.0.0"])
    return parse_route_print(out) if code == 0 else None


def public_ip() -> str | None:
    from ..providers import my_public_ip
    try:
        return my_public_ip()
    except OSError:
        return None


def nat_type(router_wan: str | None, public: str | None) -> str:
    """ok: the router holds the public address, so forwarding works.
    cgnat: the provider shares one address between customers; no forward helps.
    double: another router sits in front of this one.
    unknown: the router would not say."""
    if not router_wan:
        return "unknown"
    try:
        wan = ipaddress.ip_address(router_wan)
    except ValueError:
        return "unknown"
    if wan in CGNAT:
        return "cgnat"
    if wan.is_private:
        return "double"
    if public and router_wan != public:
        return "cgnat"
    return "ok"


# -- UPnP ----------------------------------------------------------------------

class UPnPError(Exception):
    pass


def discover(timeout: float = 2.5, bind: str | None = None) -> str | None:
    """The router's UPnP description URL, or None if no router answered."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as s:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        s.settimeout(timeout)
        try:
            s.bind((bind or lan_ip() or "", 0))
            for target in SEARCH_TARGETS:
                s.sendto((f"M-SEARCH * HTTP/1.1\r\nHOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}\r\n"
                          f'MAN: "ssdp:discover"\r\nMX: 2\r\nST: {target}\r\n\r\n').encode(), SSDP_ADDR)
            while True:
                data, _ = s.recvfrom(4096)
                m = re.search(rb"^location:\s*(\S+)", data, re.IGNORECASE | re.MULTILINE)
                if m:
                    return m.group(1).decode()
        except OSError:
            return None


def _tag(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def wan_service(location: str) -> tuple[str, str]:
    """(control URL, service type) for the router's WAN connection service."""
    with urllib.request.urlopen(location, timeout=5) as res:
        root = ET.fromstring(res.read())
    base = next((n.text for n in root.iter() if _tag(n) == "URLBase" and n.text), location)
    for service in root.iter():
        if _tag(service) != "service":
            continue
        fields = {_tag(c): (c.text or "").strip() for c in service}
        if any(w in fields.get("serviceType", "") for w in WAN_SERVICES):
            return urllib.parse.urljoin(base, fields["controlURL"]), fields["serviceType"]
    raise UPnPError("The router answered but offers no port forwarding service.")


def soap(control: str, service: str, action: str, args: dict[str, Any]) -> dict[str, str]:
    body = (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{service}">'
            + "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in args.items())
            + f"</u:{action}></s:Body></s:Envelope>")
    request = urllib.request.Request(control, data=body.encode(), method="POST", headers={
        "Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{service}#{action}"'})
    try:
        with urllib.request.urlopen(request, timeout=8) as res:
            root = ET.fromstring(res.read())
    except urllib.error.HTTPError as exc:
        try:
            root = ET.fromstring(exc.read())
            detail = {_tag(n): n.text for n in root.iter()}
            raise UPnPError(f"{detail.get('errorDescription') or 'refused'} "
                            f"(code {detail.get('errorCode', exc.code)})") from exc
        except ET.ParseError:
            raise UPnPError(f"The router refused: HTTP {exc.code}") from exc
    response = next((n for n in root.iter() if _tag(n) == f"{action}Response"), None)
    return {_tag(c): (c.text or "") for c in response} if response is not None else {}


class Router:
    """A UPnP router that has been found. find() returns None if there is none."""

    def __init__(self, control: str, service: str) -> None:
        self.control, self.service = control, service

    @classmethod
    def find(cls) -> "Router | None":
        location = discover()
        if not location:
            return None
        try:
            return cls(*wan_service(location))
        except (UPnPError, OSError, ET.ParseError):
            return None

    def external_ip(self) -> str | None:
        try:
            return soap(self.control, self.service, "GetExternalIPAddress", {}).get("NewExternalIPAddress")
        except (UPnPError, OSError):
            return None

    def mapping(self, port: int) -> str | None:
        """The LAN address a UDP port forwards to, or None."""
        try:
            got = soap(self.control, self.service, "GetSpecificPortMappingEntry", {
                "NewRemoteHost": "", "NewExternalPort": port, "NewProtocol": "UDP"})
            return got.get("NewInternalClient")
        except (UPnPError, OSError):
            return None

    def add(self, port: int, client: str, label: str) -> None:
        args = {"NewRemoteHost": "", "NewExternalPort": port, "NewProtocol": "UDP",
                "NewInternalPort": port, "NewInternalClient": client, "NewEnabled": 1,
                "NewPortMappingDescription": label, "NewLeaseDuration": 0}
        try:
            soap(self.control, self.service, "AddPortMapping", args)
        except UPnPError:
            # Some routers only accept leases that expire. The app re-adds its
            # mappings each time it starts, so a week is plenty.
            soap(self.control, self.service, "AddPortMapping", {**args, "NewLeaseDuration": 604800})

    def remove(self, port: int) -> None:
        soap(self.control, self.service, "DeletePortMapping",
             {"NewRemoteHost": "", "NewExternalPort": port, "NewProtocol": "UDP"})


# -- Windows Firewall ----------------------------------------------------------

def rule_name(name: str) -> str:
    return f"Valheim Deploy {name}"


def firewall_rule_exists(name: str) -> bool | None:
    """None when it cannot be checked, such as off Windows."""
    if not IS_WINDOWS:
        return None
    code, out = run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={rule_name(name)}"])
    return code == 0 and "No rules match" not in out


def firewall_commands(action: str, name: str, port: int) -> list[list[str]]:
    rule = f"name={rule_name(name)}"
    delete = ["netsh", "advfirewall", "firewall", "delete", "rule", rule]
    if action == "remove":
        return [delete]
    return [delete, ["netsh", "advfirewall", "firewall", "add", "rule", rule, "dir=in", "action=allow",
                     "protocol=UDP", f"localport={port}-{port + 1}", "profile=any",
                     f"program={game_dir() / 'valheim_server.exe'}"]]


def apply_firewall(action: str, name: str, port: int) -> int:
    """Run as administrator: the helper the app launches through UAC."""
    code = 0
    for argv in firewall_commands(action, name, port):
        code = run(argv)[0]
    return code


def elevate_firewall(action: str, name: str, port: int) -> bool:
    """Ask Windows for admin rights (the UAC prompt) and change the rule.
    False if the user said no or it failed."""
    from ctypes import wintypes

    class Info(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
                    ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
                    ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
                    ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    if getattr(sys, "frozen", False):
        exe, params = sys.executable, f"--firewall {action} {name} {port}"
    else:
        exe, params = sys.executable, f"-m vhdeploy.winapp --firewall {action} {name} {port}"
    info = Info()
    info.cbSize = ctypes.sizeof(Info)
    info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb, info.lpFile, info.lpParameters, info.nShow = "runas", exe, params, 0
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        return False  # the user declined the prompt
    ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 60000)
    code = wintypes.DWORD()
    ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(info.hProcess)
    return code.value == 0


# -- the checklist the Network tab shows ---------------------------------------

_router: tuple[float, Router | None] = (0.0, None)
_router_lock = threading.Lock()


def router(refresh: bool = False) -> Router | None:
    """The UPnP router, looked up at most every five minutes: discovery waits
    a few seconds for answers, and most networks never change."""
    global _router
    with _router_lock:
        if refresh or time.time() - _router[0] > 300:
            _router = (time.time(), Router.find())
        return _router[1]


def status(rec: dict[str, Any], refresh: bool = False) -> dict[str, Any]:
    port, name = rec["settings"]["port"], rec["name"]
    lan, gw, public = lan_ip(), gateway(), public_ip()
    found = router(refresh)
    wan = found.external_ip() if found else None
    mapped = {p: found.mapping(p) for p in (port, port + 1)} if found else {}
    previous = rec.get("network", {}).get("public_ip")
    return {
        "ports": [port, port + 1],
        "lan_ip": lan,
        "gateway": gw,
        "router_page": f"http://{gw}" if gw else None,
        "public_ip": public,
        "public_ip_changed": bool(previous and public and previous != public),
        "previous_public_ip": previous,
        "upnp": {"available": found is not None,
                 "forwarded": bool(found and lan and all(v == lan for v in mapped.values())),
                 "elsewhere": sorted({v for v in mapped.values() if v and v != lan})},
        "router_wan_ip": wan,
        "nat": nat_type(wan, public),
        "firewall": {"supported": IS_WINDOWS, "rule": firewall_rule_exists(name)},
        "join": {"internet": f"{public}:{port}" if public else None,
                 "lan": f"{lan}:{port}" if lan else None},
    }


def remember_public_ip(rec: dict[str, Any], public: str | None) -> None:
    if public:
        rec.setdefault("network", {})["public_ip"] = public
        rec["address"] = public
        registry.save(rec)


def open_ports(rec: dict[str, Any]) -> str:
    found, lan = router(refresh=True), lan_ip()
    if not found:
        raise SystemExit("No router answered a UPnP request. UPnP may be turned off on your router: "
                         "use the manual steps instead, or turn UPnP on in the router's settings.")
    if not lan:
        raise SystemExit("Could not work out this PC's address on your network.")
    port = rec["settings"]["port"]
    try:
        for p in (port, port + 1):
            found.add(p, lan, f"Valheim {rec['name']}")
    except (UPnPError, OSError) as exc:
        raise SystemExit(f"The router refused to open the ports: {exc}. Use the manual steps instead.") from exc
    rec.setdefault("network", {})["upnp"] = True
    registry.save(rec)
    return f"Your router now forwards UDP {port}-{port + 1} to this PC ({lan})."


def close_ports(rec: dict[str, Any]) -> None:
    found = router()
    if found:
        for p in (rec["settings"]["port"], rec["settings"]["port"] + 1):
            try:
                found.remove(p)
            except (UPnPError, OSError):
                pass
    rec.setdefault("network", {})["upnp"] = False


def refresh_mappings(recs: list[dict[str, Any]]) -> None:
    """Re-add mappings the app made before: some routers forget them on a
    reboot, and some only allow leases that expire."""
    for rec in recs:
        if rec.get("network", {}).get("upnp"):
            try:
                open_ports(rec)
            except SystemExit:
                pass
