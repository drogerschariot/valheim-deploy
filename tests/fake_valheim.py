#!/usr/bin/env python3
"""A stand-in for valheim_server.x86_64: answers A2S, logs the real lines the
agent parses, and saves only on SIGINT, like the real thing."""
import signal, socket, struct, sys, time

args = sys.argv[1:]
flag = lambda n: args[args.index(n) + 1]
port = int(flag("-port")) + 1
print("Game server connected", flush=True)
print("Got connection SteamID 76561198000000000", flush=True)
print("Got character ZDOID from Ragnar : 123:1", flush=True)
print("World save (5/5) done. Total time [812ms]", flush=True)
print("Connections 1 ZDOS:4242  sent:10 recv:20", flush=True)
if "-crossplay" in args:
    print("Session with join code 123456", flush=True)

def stop(*_):
    print("FAKE: saved on SIGINT", flush=True)
    sys.exit(0)
signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, signal.SIG_IGN)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", port))
info = (b"\xff\xff\xff\xffI\x11" + flag("-name").encode() + b"\0" + flag("-world").encode()
        + b"\0valheim\0Valheim\0" + struct.pack("<H", 0) + bytes([1, 10]))
while True:
    _, addr = sock.recvfrom(1024)
    sock.sendto(info, addr)
