"""Run with: python3 -m unittest discover tests"""
import io, json, os, signal, socket, subprocess, sys, tarfile, tempfile, threading, time, unittest, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "host")]
TMP = tempfile.mkdtemp()
os.environ["VALHEIM_DEPLOY_HOME"] = TMP

import a2s, agent, launcher  # noqa: E402
from vhdeploy import agent_client, cli, dashboard, jobs, output, paths, registry, settings, ssh  # noqa: E402
from vhdeploy.providers import aws as aws_mod, extract_tar_stream, hetzner, sizing  # noqa: E402
from vhdeploy.ui import UI  # noqa: E402


def free_udp():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def base_settings(**over):
    return {**json.loads(json.dumps(settings.DEFAULTS)), **over}


class Packaging(unittest.TestCase):
    def test_paths_from_source(self):
        self.assertTrue((paths.host_dir() / "install.py").exists())
        self.assertTrue((paths.static_dir() / "wizard.js").exists())

    def test_paths_when_frozen(self):
        with mock.patch.object(sys, "_MEIPASS", "/bundle", create=True), \
             mock.patch.object(sys, "executable", "/x/valheim-deploy-windows.exe"):
            self.assertEqual(paths.host_dir(), Path("/bundle/host"))
            self.assertEqual(paths.static_dir(), Path("/bundle/vhdeploy/static"))
            self.assertEqual(paths.command(), "valheim-deploy-windows.exe")

    def test_bundle_holds_everything_the_host_needs(self):
        names = set(tarfile.open(fileobj=io.BytesIO(ssh.bundle({}))).getnames())
        self.assertEqual(names, set(ssh.HOST_FILES))


class TarStream(unittest.TestCase):
    def test_extracts_and_refuses_escapes(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for name, data in (("./good.tar.gz", b"ok"),):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        buf.seek(0)
        dest = Path(tempfile.mkdtemp())
        self.assertEqual(extract_tar_stream(buf, dest), 0)
        self.assertEqual((dest / "good.tar.gz").read_bytes(), b"ok")

        evil = io.BytesIO()
        with tarfile.open(fileobj=evil, mode="w") as tar:
            info = tarfile.TarInfo("../escaped")
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        evil.seek(0)
        self.assertEqual(extract_tar_stream(evil, dest), 1)
        self.assertFalse((dest.parent / "escaped").exists())


class JobRunner(unittest.TestCase):
    def wait(self, job):
        for _ in range(200):
            if job.state != "running":
                return job
            time.sleep(.01)
        self.fail("job did not finish")

    def test_lifecycle_and_failure_reason(self):
        j = jobs.Jobs()
        done = self.wait(j.start("a", "x", lambda ui: ui.say("hello")))
        self.assertEqual((done.state, list(done.lines)), ("done", ["hello"]))

        def boom(ui):
            raise SystemExit("clear reason")
        failed = self.wait(j.start("a", "x", boom))
        self.assertEqual((failed.state, failed.error), ("failed", "clear reason"))

    def test_one_job_per_server(self):
        j, gate = jobs.Jobs(), threading.Event()
        first = j.start("a", "x", lambda ui: gate.wait(5))
        with self.assertRaises(jobs.Busy):
            j.start("a", "y", lambda ui: None)
        j.start("b", "x", lambda ui: None)  # another server is unaffected
        gate.set()
        self.wait(first)

    def test_subprocess_output_reaches_the_job(self):
        j = jobs.Jobs()
        done = self.wait(j.start("a", "x", lambda ui: output.stream(
            [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"], b"from stdin")))
        self.assertIn("FROM STDIN", list(done.lines))

    def test_job_ui_never_prompts(self):
        ui = jobs.JobUI(jobs.Job("a", "x"), {"host": "aws"})
        self.assertFalse(ui.show_info)
        self.assertFalse(ui.confirm("understood", "?", False))
        with self.assertRaises(SystemExit):
            ui.secret("hcloud_token", "?")


class Settings(unittest.TestCase):
    def test_defaults_valid(self):
        settings.validate(base_settings())

    def test_rejects(self):
        for bad in ({"preset": "Nightmare"}, {"modifiers": {"combat": "normal"}},
                    {"modifiers": {"bogus": "x"}}, {"world_keys": ["nope"]}, {"admins": ["bob"]},
                    {"port": 80}, {"backup_time": "25:00"}, {"world_name": "../etc"}, {"typo": 1}):
            with self.assertRaises(ValueError, msg=bad):
                settings.validate(base_settings(**bad))

    def test_password_rules(self):
        with self.assertRaises(ValueError):
            settings.check_password("abc", "x")
        with self.assertRaises(ValueError):
            settings.check_password("Secret", "My SECRET place")
        self.assertEqual(settings.check_password("hunter22", "Viking"), "hunter22")

    def test_slug(self):
        with self.assertRaises(ValueError):
            settings.check_slug("Bad Name")

    def test_sizing(self):
        self.assertEqual([sizing(n) for n in (2, 6, 20)], [4, 8, 16])


class Launcher(unittest.TestCase):
    def test_preset_precedes_modifiers_and_quoting_is_safe(self):
        cfg = settings.host_config("w", base_settings(
            server_name='Bob\'s "World" $(reboot)', preset="Hard", crossplay=True,
            modifiers={"raids": "less", "combat": "hard"}, world_keys=["nomap"]))
        argv = launcher.build_argv(cfg, "pw12345", "/bin/game")
        self.assertIn('Bob\'s "World" $(reboot)', argv)
        self.assertLess(argv.index("-preset"), argv.index("-modifier"))
        self.assertEqual(argv[argv.index("-modifier") + 1], "combat")  # sorted
        self.assertIn("-crossplay", argv)

    def test_game_env_drops_secrets(self):
        cfg = settings.host_config("w", base_settings())
        with mock.patch.dict(os.environ, {"VALHEIM_SERVER_PASSWORD": "x", "VALHEIM_AGENT_TOKEN": "y"}):
            env = launcher.game_env(cfg)
        self.assertNotIn("VALHEIM_AGENT_TOKEN", env)
        self.assertNotIn("VALHEIM_SERVER_PASSWORD", env)
        self.assertEqual(env["SteamAppId"], "892970")


class Parsers(unittest.TestCase):
    def test_flags_hide_password(self):
        out = agent.parse_flags(["bin", "-name", "A", "-password", "s3cret", "-public", "1",
                                 "-modifier", "raids", "less", "-setkey", "nomap", "-crossplay"])
        self.assertEqual(out["flags"]["password"], "(hidden)")
        self.assertNotIn("s3cret", json.dumps(out))
        self.assertEqual(out["modifiers"], {"raids": "less"})
        self.assertEqual(out["keys"], ["nomap"])
        self.assertIs(out["flags"]["crossplay"], True)

    def test_activity(self):
        out = agent.parse_activity([
            "2026-01-01T00:00:00 Got character ZDOID from Ragnar : 1:1",
            "2026-01-01T00:01:00 Got character ZDOID from Lagertha : 2:1",
            "2026-01-01T00:02:00 Got character ZDOID from Ragnar : 1:1",
            "2026-01-01T00:03:00 World save (5/5) done. Total time [812ms]",
            "2026-01-01T00:04:00 Session with join code 998877",
        ])
        self.assertEqual([p["name"] for p in out["recent_players"]], ["Ragnar", "Lagertha"])
        self.assertEqual(out["last_save"]["duration_ms"], 812)
        self.assertEqual(out["join_code"], "998877")


class AgentSupervised(unittest.TestCase):
    """The real agent, supervising a fake game, driven through the real client."""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())
        cls.port = free_udp()
        cls.agent_port = free_udp()
        cls.token = "t" * 24
        cfg = settings.host_config("t1", base_settings(port=cls.port - 1, server_name="Test Hall", world_name="Midgard"))
        cfg["instance_dir"] = str(cls.dir / "inst")
        (cls.dir / "inst" / "data").mkdir(parents=True)
        (cls.dir / "inst" / "server").mkdir()
        (cls.dir / "t1.json").write_text(json.dumps(cfg))
        cls.fake = cls.dir / "game"
        cls.fake.write_text(f"#!/bin/sh\nexec {sys.executable} {ROOT / 'tests/fake_valheim.py'} \"$@\"\n")
        cls.fake.chmod(0o755)
        env = {**os.environ, "VALHEIM_CONFIG_DIR": str(cls.dir), "VALHEIM_SKIP_STEAM": "1",
               "VALHEIM_BINARY": str(cls.fake), "VALHEIM_AGENT_TOKEN": cls.token,
               "VALHEIM_SERVER_PASSWORD": "pw12345", "VALHEIM_AGENT_BIND": "127.0.0.1",
               "VALHEIM_AGENT_PORT": str(cls.agent_port), "VALHEIM_BIN_DIR": str(cls.dir)}
        cls.proc = subprocess.Popen([sys.executable, str(ROOT / "host/agent.py"), "t1", "--supervise"],
                                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", cls.agent_port), .2).close()
                break
            except OSError:
                time.sleep(.1)
        time.sleep(1.5)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(10)

    def req(self, path, body=None, token=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.agent_port}{path}",
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {token or self.token}"})
        try:
            with urllib.request.urlopen(request, timeout=20) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_1_auth(self):
        self.assertEqual(self.req("/api/status", token="wrong")[0], 401)

    def test_2_status(self):
        code, body = self.req("/api/status")
        self.assertEqual(code, 200)
        self.assertTrue(body["service"]["ready"])
        self.assertEqual(body["query"]["name"], "Test Hall")
        self.assertEqual(body["query"]["players"], 1)
        self.assertEqual(body["activity"]["recent_players"][0]["name"], "Ragnar")

    def test_3_lists(self):
        code, body = self.req("/api/list", {"list": "banned", "op": "add", "entry": "Steam_1"})
        self.assertEqual((code, body["entries"]), (200, ["Steam_1"]))
        self.assertEqual(self.req("/api/list", {"list": "banned", "op": "add", "entry": "x; rm"})[0], 400)

    def test_4_stop_needs_confirm_and_saves_on_sigint(self):
        self.assertEqual(self.req("/api/action", {"action": "stop"})[0], 428)
        self.assertEqual(self.req("/api/action", {"action": "stop", "confirm": True})[0], 200)
        time.sleep(.5)
        self.assertTrue(any("saved on SIGINT" in l for l in self.req("/api/logs")[1]["lines"]))
        self.assertEqual(self.req("/api/action", {"action": "start"})[0], 200)


class FakeHetzner(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *a):
        pass

    def _send(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        FakeHetzner.calls.append(("GET", self.path, None))
        if self.path.startswith("/locations"):
            self._send({"locations": [{"name": "ash", "city": "Ashburn", "country": "US"}]})
        elif self.path.startswith("/server_types"):
            p = lambda g: [{"location": "ash", "price_monthly": {"gross": g}}]  # noqa: E731
            self._send({"server_types": [
                {"name": "cax21", "architecture": "arm", "memory": 8, "disk": 80, "cores": 4, "cpu_type": "shared", "prices": p("6.00")},
                {"name": "cpx21", "architecture": "x86", "memory": 4, "disk": 80, "cores": 3, "cpu_type": "shared", "prices": p("9.00")},
                {"name": "cpx31", "architecture": "x86", "memory": 8, "disk": 160, "cores": 4, "cpu_type": "shared", "prices": p("16.00")},
                {"name": "old", "architecture": "x86", "memory": 8, "disk": 80, "cores": 2, "cpu_type": "shared", "prices": p("1.00"), "deprecation": {"announced": "2025-01-01T00:00:00+00:00"}},
            ]})
        else:
            self._send({"server": {"id": 7, "status": "running", "public_net": {"ipv4": {"ip": "203.0.113.9"}}}})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeHetzner.calls.append(("POST", self.path, body))
        self._send({"ssh_key": {"id": 1}, "firewall": {"id": 2},
                    "server": {"id": 7, "status": "initializing"}})

    def do_DELETE(self):
        FakeHetzner.calls.append(("DELETE", self.path, None))
        self._send({})


class Hetzner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), FakeHetzner)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.env = mock.patch.dict(os.environ, {"HCLOUD_API": f"http://127.0.0.1:{cls.srv.server_port}", "HCLOUD_TOKEN": "x"})
        cls.env.start()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.srv.shutdown()

    def test_arm_and_deprecated_filtered_cheapest_first(self):
        self.assertEqual([p["name"] for p in hetzner.plans("ash", 4)], ["cpx21", "cpx31"])
        self.assertEqual([p["name"] for p in hetzner.plans("ash", 8)], ["cpx31"])

    def test_create_then_destroy_cleans_up(self):
        rec = {"name": "hz", "provider": "hetzner", "players": 4, "settings": base_settings(), "secrets": {},
               "resources": {"location": "ash", "plan": "cpx21", "monthly": 9.0}}
        prov = hetzner.Hetzner()
        with mock.patch("vhdeploy.providers.hetzner.my_public_ip", return_value="198.51.100.4"):
            prov.create(UI({}), rec)
        self.assertEqual(rec["address"], "203.0.113.9")
        fw = next(c for c in FakeHetzner.calls if c[1] == "/firewalls")[2]
        ssh_rule, game_rule = fw["rules"]
        self.assertEqual(ssh_rule["source_ips"], ["198.51.100.4/32"])  # SSH never open to the world
        self.assertEqual(game_rule["port"], "2456-2457")
        self.assertEqual(game_rule["protocol"], "udp")
        prov.destroy(UI({}), rec)
        deleted = [c[1] for c in FakeHetzner.calls if c[0] == "DELETE"]
        self.assertEqual(deleted, ["/servers/7", "/firewalls/2", "/ssh_keys/1"])
        registry.remove("hz")


class AWS(unittest.TestCase):
    """A fake `aws` on PATH records every call."""

    def setUp(self):
        self.bin = Path(tempfile.mkdtemp())
        self.log = self.bin / "calls.log"
        shim = self.bin / "aws"
        shim.write_text(f'''#!{sys.executable}
import json, sys
a = sys.argv[1:]
open({str(self.log)!r}, "a").write(" ".join(a) + "\\n")
v = a[a.index("--output") + 2:]
out = {{"describe-vpcs": {{"Vpcs": [{{"VpcId": "vpc-1"}}]}}, "create-security-group": {{"GroupId": "sg-1"}},
       "get-parameter": {{"Parameter": {{"Value": "ami-1"}}}}, "run-instances": {{"Instances": [{{"InstanceId": "i-1"}}]}},
       "allocate-address": {{"AllocationId": "eip-1", "PublicIp": "203.0.113.20"}},
       "describe-security-groups": {{"SecurityGroups": [{{"IpPermissions": [{{"FromPort": 22, "IpRanges": [{{"CidrIp": "1.1.1.1/32"}}]}}]}}]}}}}
print(json.dumps(out.get(v[1], {{}})))
''')
        shim.chmod(0o755)
        self.path = mock.patch.dict(os.environ, {"PATH": f"{self.bin}:{os.environ['PATH']}"})
        self.path.start()

    def tearDown(self):
        self.path.stop()

    def test_lifecycle(self):
        rec = {"name": "aw", "provider": "aws", "players": 4, "settings": base_settings(), "secrets": {},
               "resources": {"region": "us-east-1", "instance_type": "t3.medium"}}
        prov = aws_mod.AWS()
        with mock.patch("vhdeploy.providers.aws.my_public_ip", return_value="198.51.100.4"):
            prov.create(UI({}), rec)
        self.assertEqual(rec["ssh"]["user"], "ubuntu")
        self.assertEqual(rec["address"], "203.0.113.20")
        prov.allow_ip(UI({}), rec, "198.51.100.9")
        prov.destroy(UI({}), rec)
        log = self.log.read_text()
        self.assertIn('"CidrIp": "198.51.100.4/32"', log)
        self.assertNotIn('"FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0"', log)
        order = [log.index(v) for v in ("terminate-instances", "release-address", "delete-security-group", "delete-key-pair")]
        self.assertEqual(order, sorted(order))
        registry.remove("aw")


class Wizard(unittest.TestCase):
    def tearDown(self):
        for r in registry.all_servers():
            registry.remove(r["name"])

    def test_must_accept_advanced_notice(self):
        path = Path(TMP) / "a.json"
        path.write_text(json.dumps({"understood": False}))
        self.assertEqual(cli.cmd_new(mock.Mock(answers=str(path))), 1)
        self.assertEqual(registry.all_servers(), [])

    def test_ports_do_not_collide_on_one_machine(self):
        one = {"name": "a", "provider": "local", "settings": base_settings(), "ssh": {}}
        registry.save(one)
        self.assertEqual(cli.next_port({"provider": "local"}), 2466)
        self.assertEqual(cli.next_port({"provider": "hetzner"}), 2456)
        registry.save({"name": "b", "provider": "existing", "settings": base_settings(), "ssh": {"host": "h"}})
        self.assertEqual(cli.next_port({"provider": "existing", "ssh": {"host": "h"}}), 2466)
        self.assertEqual(cli.next_port({"provider": "existing", "ssh": {"host": "other"}}), 2456)


class Dashboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        cls.client = mock.Mock(spec=agent_client.AgentClient)
        cls.client.call.return_value = (200, {"service": {"active": "active"}})
        cls.jobs = jobs.Jobs()
        cls.srv = dashboard.ThreadingHTTPServer(("127.0.0.1", cls.port), dashboard.make_handler("tok" * 8, cls.port, cls.client, cls.jobs))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        for n in ("alpha", "beta"):
            registry.save({"name": n, "provider": "local", "address": "203.0.113.1", "ssh": {},
                           "settings": base_settings(server_name=n.title()),
                           "secrets": {"password": "pw12345", "agent_token": "SECRET-AGENT-TOKEN"}})

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        registry.remove("alpha")
        registry.remove("beta")

    def http(self, method, path, body=None, cookie=None, host=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        headers = {"Host": host or f"127.0.0.1:{self.port}", "Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        c.request(method, path, None if body is None else json.dumps(body), headers)
        r = c.getresponse()
        return r.status, r.read(), r.getheader("Set-Cookie"), r.getheader("Location")

    def login(self):
        return self.http("GET", f"/?login={'tok' * 8}")[2].split(";")[0]

    def test_rejects_rebinding_host(self):
        self.assertEqual(self.http("GET", "/", host="evil.example")[0], 403)

    def test_requires_sign_in(self):
        self.assertEqual(self.http("GET", "/api/servers")[0], 401)
        self.assertEqual(self.http("GET", "/static/app.js")[0], 404)
        self.assertEqual(self.http("POST", "/api/login", {"token": "no"})[0], 401)

    def test_login_redirect_strips_token(self):
        status, _, cookie, loc = self.http("GET", f"/?login={'tok' * 8}")
        self.assertEqual((status, loc), (302, "/"))
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

    def test_lists_all_servers_and_never_leaks_agent_token(self):
        cookie = self.login()
        _, raw, *_ = self.http("GET", "/api/servers", cookie=cookie)
        self.assertEqual([s["name"] for s in json.loads(raw)["servers"]], ["alpha", "beta"])
        self.assertNotIn(b"SECRET-AGENT-TOKEN", raw)
        _, raw, *_ = self.http("GET", "/api/servers/beta/connect", cookie=cookie)
        info = json.loads(raw)
        self.assertEqual(info["join"], "203.0.113.1:2456")
        self.assertEqual(info["password"], "pw12345")
        self.assertTrue(info["notes"])
        self.assertNotIn(b"SECRET-AGENT-TOKEN", raw)

    def test_proxies_to_the_chosen_server(self):
        cookie = self.login()
        self.http("POST", "/api/servers/beta/action", {"action": "restart"}, cookie=cookie)
        rec, path, body = self.client.call.call_args[0]
        self.assertEqual((rec["name"], path, body), ("beta", "/api/action", {"action": "restart"}))

    def test_unreachable_server_is_a_message_not_a_crash(self):
        cookie = self.login()
        self.client.call.side_effect = agent_client.AgentError("no route")
        status, raw, *_ = self.http("GET", "/api/servers/alpha/status", cookie=cookie)
        self.client.call.side_effect = None
        self.assertEqual(status, 502)
        self.assertTrue(json.loads(raw)["offline"])

    def job_done(self, job_id, cookie):
        for _ in range(300):
            view = json.loads(self.http("GET", f"/api/jobs/{job_id}", cookie=cookie)[1])
            if view["state"] != "running":
                return view
            time.sleep(.02)
        self.fail("job did not finish")

    def test_post_requires_json_content_type(self):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("POST", "/api/login", "token=x", {"Host": f"127.0.0.1:{self.port}",
                                                    "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(c.getresponse().status, 415)

    def test_new_routes_require_sign_in(self):
        for path in ("/api/servers", "/api/servers/alpha/destroy", "/api/wizard/hetzner-token"):
            self.assertEqual(self.http("POST", path, {})[0], 401, path)
        self.assertEqual(self.http("GET", "/api/wizard/options")[0], 401)
        self.assertEqual(self.http("GET", "/api/jobs/x")[0], 401)

    def test_wizard_lists_hosts_with_notice(self):
        with mock.patch("vhdeploy.providers.local.Local.preflight", return_value="Docker is not running"):
            body = json.loads(self.http("GET", "/api/wizard/options", cookie=self.login())[1])
        self.assertEqual([h["key"] for h in body["hosts"]], cli.HOST_CHOICES)
        self.assertIn("ADVANCED", body["notice"])
        self.assertEqual(next(h for h in body["hosts"] if h["key"] == "local")["problem"], "Docker is not running")
        self.assertGreaterEqual(len(body["suggested_password"]), 5)

    def test_unknown_wizard_host(self):
        self.assertEqual(self.http("GET", "/api/wizard/options?host=nope", cookie=self.login())[0], 400)

    def test_create_refuses_without_accepting_the_warning(self):
        cookie = self.login()
        status, raw, *_ = self.http("POST", "/api/servers", {"name": "gamma", "host": "local", "go": True}, cookie=cookie)
        self.assertEqual(status, 202)
        view = self.job_done(json.loads(raw)["job"], cookie)
        self.assertIn("Stopped. Nothing was created.", view["lines"])
        self.assertFalse(registry.path_for("gamma").exists())

    def test_create_rejects_bad_and_duplicate_names(self):
        cookie = self.login()
        self.assertEqual(self.http("POST", "/api/servers", {"name": "Bad Name"}, cookie=cookie)[0], 409)
        self.assertEqual(self.http("POST", "/api/servers", {"name": "alpha"}, cookie=cookie)[0], 409)

    def test_destroy_needs_the_typed_name(self):
        cookie = self.login()
        self.assertEqual(self.http("POST", "/api/servers/alpha/destroy", {"confirm_name": "beta"}, cookie=cookie)[0], 409)
        self.assertTrue(registry.path_for("alpha").exists())

    def test_fix_ip_is_cloud_only(self):
        self.assertEqual(self.http("POST", "/api/servers/alpha/allow-my-ip", {}, cookie=self.login())[0], 409)

    def test_incomplete_cloud_server_offers_delete_not_retry(self):
        registry.save({"name": "stuck", "provider": "hetzner", "status": "planned", "ssh": {},
                       "settings": base_settings(), "secrets": {}})
        try:
            body = json.loads(self.http("GET", "/api/servers", cookie=self.login())[1])
            stuck = next(s for s in body["servers"] if s["name"] == "stuck")
            self.assertFalse(stuck["can_retry"])
            self.assertTrue(stuck["cloud"])
        finally:
            registry.remove("stuck")

    def test_hetzner_token_not_stored_when_rejected_nor_echoed(self):
        with mock.patch("vhdeploy.dashboard.save_token", side_effect=SystemExit("no")):
            status, raw, *_ = self.http("POST", "/api/wizard/hetzner-token", {"token": "SECRET-T"}, cookie=self.login())
        self.assertEqual(status, 400)
        self.assertNotIn(b"SECRET-T", raw)

    def test_unknown_server_and_traversal(self):
        cookie = self.login()
        self.assertEqual(self.http("GET", "/api/servers/nope/status", cookie=cookie)[0], 404)
        self.assertEqual(self.http("GET", "/api/servers/../../x/status", cookie=cookie)[0], 404)


if __name__ == "__main__":
    unittest.main()
