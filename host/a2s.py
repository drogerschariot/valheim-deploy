"""Minimal Steam A2S_INFO client. Used by the agent (over loopback) and by the
controller (over the internet, to answer "can players actually reach this?")."""

from __future__ import annotations

import socket
from typing import Any


def query(host: str, port: int, timeout: float = 3.0) -> dict[str, Any]:
    """Ask the server itself how many players are on.

    This is the only authoritative source for the player count. The log
    reports connections but has no dependable disconnect line, so counting
    joins there drifts upward forever.

    Never raises: a dashboard has to stay readable when the server is sick.
    """
    request = b"\xff\xff\xff\xffTSource Engine Query\x00"
    sock = None
    try:
        family = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)[0][0]
        sock = socket.socket(family, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(request, (host, port))
        data, _ = sock.recvfrom(4096)
        if data[4:5] == b"A":  # challenge; resend with the token appended
            sock.sendto(request + data[5:9], (host, port))
            data, _ = sock.recvfrom(4096)
        if data[4:5] != b"I":
            return {"reachable": False, "reason": "unexpected reply"}

        body = data[6:]  # skip the type and protocol bytes

        def cstr(buf: bytes) -> tuple[str, bytes]:
            end = buf.index(b"\x00")
            return buf[:end].decode("utf-8", "replace"), buf[end + 1:]

        name, body = cstr(body)
        world, body = cstr(body)
        _folder, body = cstr(body)
        _game, body = cstr(body)
        body = body[2:]  # app id
        return {
            "reachable": True,
            "name": name,
            "world": world,
            "players": body[0],
            "max_players": body[1],
        }
    except Exception as exc:
        return {"reachable": False, "reason": type(exc).__name__}
    finally:
        if sock:
            sock.close()
