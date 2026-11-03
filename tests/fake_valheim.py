#!/usr/bin/env python3
"""A stand-in for the Valheim server: answers A2S, logs the real lines the
agent parses, and saves only on Ctrl+C (SIGINT), like the real thing.

With -logFile it writes there, as valheim_server.exe does on Windows;
otherwise it prints, as the Linux server does under systemd."""
import signal, socket, struct, sys, time

args = sys.argv[1:]
flag = lambda n: args[args.index(n) + 1]
port = int(flag("-port")) + 1
log = open(flag("-logFile"), "a", encoding="utf-8") if "-logFile" in args else sys.stdout


def say(line):
    log.write(line + "\n")
    log.flush()


say("Game server connected")
say("Got connection SteamID 76561198000000000")
say("Got character ZDOID from Ragnar : 123:1")
say("World save (5/5) done. Total time [812ms]")
say("Connections 1 ZDOS:4242  sent:10 recv:20")
if "-crossplay" in args:
    say("Session with join code 123456")


def stop(*_):
    say("FAKE: saved on SIGINT")
    sys.exit(0)


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, signal.SIG_IGN)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", port))
# A timeout, so Python gets back to its signal handler: on Windows a blocking
# receive is not interrupted by Ctrl+C.
sock.settimeout(0.5)
info = (b"\xff\xff\xff\xffI\x11" + flag("-name").encode() + b"\0" + flag("-world").encode()
        + b"\0valheim\0Valheim\0" + struct.pack("<H", 0) + bytes([1, 10]))
while True:
    try:
        _, addr = sock.recvfrom(1024)
    except socket.timeout:
        continue
    sock.sendto(info, addr)
