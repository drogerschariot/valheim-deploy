#!/usr/bin/env python3
"""CI check of the installed Windows app, with a fake game standing in for
Valheim. Run on Windows after a silent install:

  dist\\ValheimDeploy-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
  python packaging\\smoke_windows.py

Proves the parts only a real install can: the packaged app starts with no
window, creates and runs a server, auto-starts it on the next launch, and
--quit stops it through the packaged Ctrl+C helper so the world is saved.
Then uninstalls, and checks the worlds were kept.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Valheim Deploy"
APP = APP_DIR / "ValheimDeploy.exe"
DATA = Path(os.environ["LOCALAPPDATA"]) / "ValheimDeploy"
BASE = "http://127.0.0.1:8456"
ENV = {**os.environ, "VALHEIM_BINARY_ARGV": json.dumps([sys.executable, str(ROOT / "tests" / "fake_valheim.py")])}


def step(text):
    print(f"--- {text}", flush=True)


def wait(check, seconds, what):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if check():
                return
        except OSError:
            pass
        time.sleep(1)
    raise SystemExit(f"FAILED: {what} within {seconds}s")


def up():
    with urllib.request.urlopen(f"{BASE}/static/login.css", timeout=3):
        return True


def call(method, path, body=None, cookie=""):
    request = urllib.request.Request(f"{BASE}{path}", method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Cookie": cookie})
    try:
        with urllib.request.urlopen(request, timeout=30) as res:
            return res.status, json.loads(res.read()), res.headers.get("Set-Cookie", "")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read()), ""


def sign_in():
    token = (DATA / "dashboard.token").read_text().strip()
    return call("POST", "/api/login", {"token": token})[2].split(";")[0]


def start_app():
    proc = subprocess.Popen([str(APP), "--no-window"], env=ENV)
    wait(up, 60, "the app answering")
    return proc


def ready(cookie):
    status, body, _ = call("GET", "/api/servers/ci/status", cookie=cookie)
    return status == 200 and body["service"]["ready"]


def quit_app(proc):
    code = subprocess.run([str(APP), "--quit"], env=ENV, timeout=300).returncode
    assert code == 0, f"--quit exited {code}"
    proc.wait(60)
    log = (DATA / "instances" / "ci" / "server.log").read_text(encoding="utf-8")
    assert "FAKE: saved on SIGINT" in log, "the world was not saved on quit:\n" + log


step("installed files")
assert APP.exists(), f"{APP} is missing"

step("first start, create a server")
proc = start_app()
cookie = sign_in()
status, body, _ = call("GET", "/api/servers", cookie=cookie)
assert body["app_mode"], body
status, body, _ = call("POST", "/api/servers", {
    "understood": True, "host": "windows", "name": "ci", "players": "4", "password": "pw12345",
    "server_name": "CI Hall", "world_name": "Midgard", "go": True}, cookie=cookie)
assert status == 202, (status, body)
job = body["job"]
wait(lambda: call("GET", f"/api/jobs/{job}", cookie=cookie)[1]["state"] != "running", 120, "the create job")
view = call("GET", f"/api/jobs/{job}", cookie=cookie)[1]
assert view["state"] == "done", "\n".join(view["lines"])
wait(lambda: ready(cookie), 60, "the server reporting ready")

step("quit saves the world")
quit_app(proc)

step("second start: the server starts by itself")
proc = start_app()
cookie = sign_in()
wait(lambda: ready(cookie), 90, "auto-start")
quit_app(proc)

step("uninstall keeps the worlds")
subprocess.run([str(APP_DIR / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], check=True)
wait(lambda: not APP.exists(), 60, "the app being removed")
assert (DATA / "servers" / "ci.json").exists(), "uninstall deleted the user's servers"
print("--- all good", flush=True)
