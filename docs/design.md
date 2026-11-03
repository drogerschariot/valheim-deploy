# Design

```
your computer                              game host (cloud VM, Linux box, or Docker)
+---------------------------+              +-----------------------------------+
| ./valheim      (CLI)      |  SSH install |  valheim-server-NAME   (the game) |
| ./valheim dashboard       |------------->|  valheim-agent-NAME    (127.0.0.1)|
|   browser <-> 127.0.0.1   |  SSH tunnel  |  valheim-backup-NAME   (timer)    |
| ~/.valheim-deploy/        |------------->|  /opt/valheim/instances/NAME      |
+---------------------------+              +-----------------------------------+
                                     players ---- UDP game ports ----^
```

- **`vhdeploy/`** runs on the owner's computer: the wizards, providers, dashboard.
- **`packaging/`** builds it into a single-file app with PyInstaller.
- **`host/`** is what lands on a game host. Standard library only.

## The Windows app

```
ValheimDeploy.exe (one process, no console)
 ├─ tray icon (pystray)
 ├─ dashboard on 127.0.0.1:8456 (dashboard.py, unchanged routes plus Network)
 │    └─ LocalAgent: host/agent.py's API, answered in-process (agent.handle)
 ├─ ServerManager: one WindowsBackend per server → valheim_server.exe
 └─ window: Edge in app mode (msedge --app=URL), else the default browser
```

- **`vhdeploy/winapp.py`** is the entry point: single instance, window, tray,
  and the `--quit`, `--send-ctrl-c` and `--firewall` helper modes.
- **`vhdeploy/winhost/`** runs the servers and checks the network.
- **`providers/windows.py`** plugs it into the same create and delete flow.

**Servers run on the PC, natively.** Valheim ships a Windows dedicated server, so
there's no Docker, VM or SSH. The app is the supervisor, so servers run while
it runs, which is why closing the window only hides it.

**The same agent API, in-process.** `host/agent.py`'s request handling is a plain
function, `handle()`. The Windows app calls it directly with its own backend and
collectors, so the dashboard and its JavaScript serve both kinds of server
without knowing which is which. `Collectors` is subclassed only where it read
`/proc`.

**An Edge app window, not a bundled browser.** Edge is on every Windows 10 and 11
PC. `--app` gives a plain window with its own taskbar entry and profile. It adds
no WebView2 or .NET dependency, and reuses the web UI as is.

**One shared game install.** Every server must run the version players have,
so one copy serves them all. **Update game** stops whatever is running, backs it
up, updates, and starts it again.

**Standard library, except the tray.** UPnP, SOAP, firewall rules and process
control use only the standard library and `ctypes`. `pystray` and `Pillow` are the
only dependencies, and only for the packaged Windows app.

**A per-user installer.** Inno Setup, per user: no admin prompt, which matters
for an unsigned app. Data lives in `%LOCALAPPDATA%\ValheimDeploy` and survives
uninstalling unless the user asks. Install and uninstall both run `--quit` first,
so an upgrade can't kill a server mid-save.

### Windows traps

- **Valheim saves only on Ctrl+C, and Windows can't signal another process.**
  `winhost/ctrlc.py` starts a short-lived helper that attaches to the server's
  console and raises Ctrl+C on it. Two rules hold this together:
  - The game must not be started with `CREATE_NEW_PROCESS_GROUP`, which disables
    Ctrl+C for it.
  - The app itself must never call `SetConsoleCtrlHandler(NULL, TRUE)`. That
    "ignore Ctrl+C" flag is inherited, so every server started afterwards could
    never be stopped safely. Only the helper sets it.
- **The game outlives the app.** If the app is killed rather than quit, its
  servers keep running and hold their ports, so a fresh start would crash-loop.
  Each server's PID is recorded. Before starting, the app checks for a leftover
  with that PID and the right program, and stops it with Ctrl+C so it saves.
- **The Windows server writes its log only to `-logFile`.** The backend tails that
  file for logs and readiness.
- **A blocking socket read isn't interrupted by Ctrl+C on Windows.** That
  affected the fake game in tests, not the app.
- **`ipaddress.is_private` includes documentation and reserved ranges.** NAT
  detection uses only the RFC 1918 home ranges and 100.64.0.0/10.
- **Some routers accept only port mappings that expire.** The app falls back to
  a week-long mapping and re-adds its mappings each time it starts.

## Decisions

**One install path.** AWS and Hetzner create a VM and hand over to the same SSH
install as "a Linux server I already have", so all three behave identically.
Nothing goes in cloud-init: user data has small size limits and would put
secrets in provider metadata.

**No Ansible or GitHub Actions.** The infrastructure repo this came from used
both. They are right for one estate and too much to ask of someone who just
wants a server. Only Python and SSH are required.

**Dashboard on the owner's computer, agents on loopback.** The original dashboard
was safe because it was reachable only from a trusted LAN. A cloud host has no
such LAN, and publishing a plain-HTTP admin console would need TLS, certificates
and firewall rules for everyone to get right. Tunnelling over SSH keeps the
original rule, that the console is never exposed, and needs no open port.

**Docker only for "this computer".** It is the only way to run a Linux binary on
macOS and Windows. In a container there is no systemd or journal, so the agent
supervises the game itself (`SupervisedBackend`). Everything above that backend is
shared.

**Per-instance everything.** Each server has its own game install, data, units,
and sudoers file, so several can share a host and one can be removed cleanly.

**Records saved after each cloud resource.** An interrupted run can always be
cleaned up by `destroy`, so nothing is left billing unnoticed.

**A single download, with setup in the browser.** Requiring git, Python and a
terminal ruled out many Windows users. The packaged app opens the dashboard when
double-clicked. The browser wizard only collects answers: it submits them to
`cli.create_server` through the same `UI(answers)` path as the terminal, so the
rules exist once.

**AWS through a built-in client, not the CLI.** Installing and configuring the
`aws` CLI is too much to ask of someone who wants a game server, and cannot be
done from a browser. `providers/awsapi.py` signs EC2 and STS query requests
itself, so a user pastes an IAM access key and nothing else. The image comes from
DescribeImages rather than an SSM parameter, so the key needs only
AmazonEC2FullAccess. Root keys are refused.

**Guided accounts.** A provider that needs an account carries a `guide`: steps to
make one, steps to get the key, and the fields to ask for. The terminal and the
browser both show it, and credentials are checked with the provider before they
are saved.

**Long work runs as jobs.** Creating a server can take many minutes, so it runs on
a thread with captured output (`jobs.py`, `output.py`) and the page polls it. Only
one job runs per server. A job's log never includes the closing summary, because
that prints the join password.

**Plain `tarfile` for backups.** Backup bytes come from a remote machine, so they
are unpacked with the `data` filter, which stops them writing outside the target.

## Traps already paid for

Learned on the original server. Each is enforced somewhere; do not undo them.

- **Valheim saves only on SIGINT.** SIGTERM is ignored, so a stock stop loses
  everything since the last autosave. Hence `KillSignal=SIGINT` with a 120s budget,
  Docker's `STOPSIGNAL SIGINT` and 130s stop timeout, and `exec` in the launcher
  so the signal reaches the game rather than a wrapper.
- **There is no on-demand save.** Stdin commands were tested twice against
  Valheim l-1.0.12, through a pipe and a pty, and do nothing. The autosave is
  therefore 600s rather than Valheim's 1800s. Do not retry this without new
  information.
- **The server is not version pinned.** Clients auto-update and must match the
  server, so a pin locks everyone out on patch day. Safety lives in the update
  procedure: back up, update, health-check.
- **Readiness comes from the log, not the port.** The UDP socket binds before the
  world loads. It is bounded to the current run by invocation ID, since the journal
  still holds the line from the previous run.
- **`journalctl | grep -q` under pipefail returns 141** once the log is large.
  Match against a variable.
- **`-preset` must precede `-modifier`,** or the preset wipes the modifiers.
- **Valheim does not validate flags.** A bad modifier is ignored silently, so
  `settings.py` rejects it first.
- **Backups prefer the server's own auto-backups** over the live directory, which
  can be caught mid-save.

## Problems found while building this

- **Update check in the old dashboard's sandbox:** the agent's systemd sandbox
  must list the steamcmd directory as writable, or `--check` cannot run steamcmd.
- **SSH port on a forwarded box:** the port the CLI dials can differ from the one
  sshd listens on. The installer reads the real one from `$SSH_CONNECTION`, or
  enabling ufw would lock it out.
- **Admin list:** re-applying settings merges rather than replaces, so admins added
  on the dashboard survive.

## Test status

`python3 -m unittest discover tests` covers settings, launcher, parsers, packaging
paths, jobs, the real agent supervising a fake game, the dashboard's auth and
endpoints, and Hetzner and AWS against fake backends.

| Path | Status |
|---|---|
| Windows app: server runner, Ctrl+C stop, backups, leftover-server cleanup | **Run in Windows CI** with a fake game: start, ready, backup, stop through the real Ctrl+C helper with the world saved. Also unit tested on macOS |
| Windows installer: install, create a server, quit, auto-start, uninstall | **Run in Windows CI** by `packaging/smoke_windows.py` against the installed app, with a fake game |
| Windows app dashboard: wizard, Network tab, Start-on-open switch, Quit | **Clicked through in Chrome** on macOS, with a fake game and a fake router |
| UPnP | **Fake router only.** SOAP and description shapes follow the UPnP IGD spec |
| Windows Firewall rule through UAC | **Not run.** CI can't answer a UAC prompt |
| Edge app window and tray icon | **Not run.** CI has no desktop |
| Real Valheim Windows server, real SteamCMD download | **Not yet run** |
| Browser wizard (CLI dashboard): create, live progress, download backups, delete | **Run for real** in Chrome against Ubuntu 24.04 under systemd, arm64, with a fake game. The x86 check and 32-bit packages were patched out in a throwaway copy |
| Terminal wizard, SSH install, systemd units, sudo rules, ufw, tunnel, day-two commands | **Run for real** the same way, two servers on one host |
| Docker image, entrypoint, privilege drop, volumes, port publishing, SIGINT stop | **Run for real**, natively on arm64 with a fake game and without the 32-bit packages |
| Hetzner API | **Fake backend only.** Request shapes come from third-party docs and client libraries |
| AWS | **Fake EC2 endpoint only.** Request signing matches the `aws` CLI byte for byte for EC2 and STS. No real resources have been created |
| Crossplay | Join-code parsing is tested against an assumed log line, not real output |

**Before calling the Windows app ready, on a real Windows PC:**
1. Install it, then create a server, which downloads the real game.
2. Add the firewall rule, and open ports through UPnP or by hand.
3. Have a friend outside the home join.
4. Close the window and check the server keeps running.
5. Quit, and check the log shows the world saved.
6. Reopen the app and check the server starts by itself.
7. Delete the server and uninstall.

### Testing on Apple Silicon

Do not run an amd64 `systemd` container with `--privileged` on Docker Desktop.
`systemd-binfmt` deregisters Rosetta from the shared VM, and every x86 container
then fails with `exec format error` until Docker Desktop restarts. Use an arm64
systemd container, as the steps above did.
