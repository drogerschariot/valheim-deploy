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
| Browser wizard: create, live progress, download backups, delete | **Run for real** in Chrome against Ubuntu 24.04 under systemd, arm64, with a fake game. The x86 check and 32-bit packages were patched out in a throwaway copy |
| Terminal wizard, SSH install, systemd units, sudo rules, ufw, tunnel, day-two commands | **Run for real** the same way, two servers on one host |
| Docker image, entrypoint, privilege drop, volumes, port publishing, SIGINT stop | **Run for real**, natively on arm64 with a fake game and without the 32-bit packages |
| Packaged macOS app | **Run for real**: version, list, dashboard, wizard data from the bundle, second launch. Installing a server from the packaged app has not been run |
| Packaged Windows and Linux apps | **Built and smoke tested in CI** only |
| Update game, Fix my IP and Retry install buttons | Endpoints unit tested, not clicked through |
| Real Steam download and the real Valheim binary | **Not yet run through this repo** |
| Hetzner API | **Fake backend only.** Request shapes come from third-party docs and client libraries |
| AWS | **Fake EC2 endpoint only.** Request signing matches the `aws` CLI byte for byte for EC2 and STS. Query parameter names and response shapes come from the EC2 API reference. No real resources have been created |
| Account walk-throughs | Endpoints and the connect checks are unit tested. The steps and links have not been followed with a new account |
| Real x86-64 Ubuntu host | **Not yet run** |
| Windows use: icacls key permissions, paths with spaces, Docker Desktop, SSH | **Not run on a real PC** |
| Crossplay | Join-code parsing is tested against an assumed log line, not real output |

**Before publishing a release, on a real Windows PC: download the app, create a
real Hetzner server, join it from the game, and delete it. Do the same once on AWS.**

### Testing on Apple Silicon

Do not run an amd64 `systemd` container with `--privileged` on Docker Desktop.
`systemd-binfmt` deregisters Rosetta from the shared VM, and every x86 container
then fails with `exec format error` until Docker Desktop restarts. Use an arm64
systemd container, as the steps above did.
