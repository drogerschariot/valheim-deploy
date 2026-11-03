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

- **`vhdeploy/`** runs on the owner's computer: the wizard, providers, dashboard.
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

`python3 -m unittest discover tests` covers settings, launcher, parsers, the real
agent supervising a fake game, the dashboard's auth and proxying, and Hetzner and
AWS against fake backends.

| Path | Status |
|---|---|
| Wizard, SSH install, systemd units, sudo rules, ufw, tunnel, dashboard, day-two commands, destroy | **Run for real** on Ubuntu 24.04 under systemd, two servers on one host, with a fake game. The host was arm64, so the x86 check and 32-bit packages were patched out in a throwaway copy |
| Docker image, entrypoint, privilege drop, volumes, port publishing, SIGINT stop, destroy | **Run for real**, natively on arm64 with a fake game and without the 32-bit packages |
| Dashboard UI | **Run for real** in Chrome: drop-down, switching servers, every tab, no console errors |
| Real Steam download and the real Valheim binary | **Not yet run through this repo.** The update and backup logic carries over from the live server, but install, launch and readiness here have not met the real game |
| Hetzner API | **Fake backend only.** Request shapes come from third-party docs and client libraries, not a live call. Plans and server creation have never hit the real API |
| AWS | **Fake `aws` command only.** No real resources have been created |
| Real x86-64 Ubuntu host | **Not yet run** |
| Windows | **Not run** |
| Crossplay | Join-code parsing is tested against an assumed log line, not real output |

**Before announcing this, create one real Hetzner server and one real AWS server,
and join each from the game.**

### Testing on Apple Silicon

Do not run an amd64 `systemd` container with `--privileged` on Docker Desktop.
`systemd-binfmt` deregisters Rosetta from the shared VM, and every x86 container
then fails with `exec format error` until Docker Desktop restarts. Use an arm64
systemd container, as the steps above did.
