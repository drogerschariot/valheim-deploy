# Valheim Deploy

Create your own Valheim dedicated servers, as many as you like, and run them all
from one dashboard. Clone this repo; everything happens from here.

```sh
git clone https://github.com/drogerschariot/valheim-deploy.git && cd valheim-deploy
./valheim new         # guided setup, about 10 minutes
./valheim dashboard   # all your servers, in a drop-down
```

Needs Python 3.9+ and an SSH client. Nothing to `pip install`. On Windows use
`valheim` from a terminal in the repo folder (`valheim.cmd`).

> **This is an advanced setup.** You are running a real server, and you need to be
> comfortable with firewalls, public IP addresses, port forwarding (for a server
> at home), SSH, and cloud billing. If those are new to you, a paid Valheim host
> will be a much better experience. The wizard says this too, and will not
> continue until you accept it.

## Choosing where it runs

| Option | Best for | You need | Cost | Network setup |
|---|---|---|---|---|
| **Hetzner Cloud** (recommended) | An always-on server for friends, at the lowest cost | An account and API token | Live prices are shown in the wizard | Done for you |
| **AWS** | You already use AWS | `aws` CLI, logged in | Highest: see below | Done for you |
| **A Linux server you have** | A home server, Proxmox VM, or a VPS from any provider | x86-64 Ubuntu 20.04+, SSH as root or passwordless sudo | Whatever you pay now | You do it |
| **This computer** (Docker) | Trying it out, a LAN party | Docker | Electricity | You do it |

**Why Hetzner:** a game server runs flat out around the clock, which is the case
on-demand cloud pricing is worst at. It needs one API token rather than an IAM
setup, and the wizard reads plans and prices from Hetzner's API, so they are never
out of date. It has locations in the US, Europe and Singapore.

**AWS cost:** the wizard's figures are rough us-east-1 on-demand numbers, and
the instance, disk, public IP and outbound data are all billed. Check the AWS
calculator for your region.

**This computer on Apple Silicon or other ARM:** Valheim's server is x86-64 only,
so it runs under emulation. The wizard warns you. See the
[test status](#what-has-been-tested) for how that went. Pick a cloud or Linux host
for real play.

**How much RAM:** the wizard recommends 4 GB for up to 4 players, 8 GB up to 10,
and 16 GB beyond. Memory tracks how explored the world is more than how many are
playing, so a year-old world needs more than a new one. Valheim's simulation is
single-threaded, so one fast core matters more than many slow ones.

## After the wizard

It finishes by printing everything you need. Get it again at any time:

```sh
./valheim info myworld     # join address, password, ports, SSH, what is left to do
./valheim doctor myworld   # is it healthy, and can the internet reach it?
```

To join, in Valheim choose **Start Game > Join Game > Add server** and enter the
join address. Crossplay servers use a join code instead, shown on the dashboard.

If the server is at home or on your own box, you still have to open the network
yourself: forward **UDP** on the two game ports (2456-2457 for your first server)
to the machine. `info` and the dashboard's Connect tab list exactly what to do.
There is no TCP port and nothing else to open.

`doctor` tests from your own computer, so a home server can fail the reachability
check from inside your own network even when it works for everyone else, because
many routers do not loop traffic back. Ask a friend to try.

## Commands

| Command | What it does |
|---|---|
| `./valheim new` | Create a server. `--answers file.json` runs it unattended |
| `./valheim list` | All your servers |
| `./valheim info NAME` | Join address, password, and what is left to do |
| `./valheim dashboard` | The dashboard |
| `./valheim doctor NAME` | Health and reachability checks |
| `./valheim start` / `stop` / `restart NAME` | Control the game. A stop saves first |
| `./valheim backup NAME` | Take a backup now |
| `./valheim backups NAME` | Download all backups to this computer |
| `./valheim update-check NAME` | Is a game update available? |
| `./valheim update NAME` | Back up, update, and health-check |
| `./valheim set NAME key=value ...` | Change settings (see below); restarts if needed |
| `./valheim apply NAME` | Re-run the install, for example after a failure |
| `./valheim allow-my-ip NAME` | Your home IP changed and SSH stopped working (cloud servers) |
| `./valheim destroy NAME` | Delete the server **and stop its billing**. Offers to download backups first |

When Valheim updates, players cannot join until the server matches, so run
`./valheim update NAME`. Updates are never automatic on a Linux host, so one cannot
stop a game in progress. On **This computer** the server also updates each time it
starts.

## The dashboard

`./valheim dashboard` opens a page on your own computer. Pick a server from the
drop-down at the top. Each server has live status, players, host metrics, start,
stop, restart and backup buttons, admin/ban/permit lists, backups, logs, and a
**Connect** tab with the join address, password, and reachability check.

It runs on your computer because that is where your SSH keys are. It reaches each
server through an SSH tunnel, and the agent on each server listens on loopback
only, so **no admin port is ever open to the internet**. Never forward or open one.
Keep the command running while you use the page.

Valheim has no RCON, so some things do not exist: no arbitrary console commands,
no chat, and no remote kick. A ban takes effect when that player next connects.
Admins can kick in-game from the F5 console.

## Settings

`./valheim set myworld preset=Hard 'modifiers={"raids":"less"}'`

| Setting | Default | Notes |
|---|---|---|
| `server_name` | | Shown to players. Must not contain the password |
| `world_name` | `Dedicated` | **Changing it starts a new world** |
| `password` | generated | 5+ characters |
| `public` | `false` | Listed in the community browser; the password is then the only protection |
| `crossplay` | `false` | Xbox and Game Pass players, using a join code |
| `admins` | `[]` | `["Steam_7656119..."]`. Also editable on the dashboard |
| `preset` | | `Normal` `Casual` `Easy` `Hard` `Hardcore` `Immersive` `Hammer` |
| `modifiers` | `{}` | See below. Applied after the preset |
| `world_keys` | `[]` | `nobuildcost` `playerevents` `passivemobs` `nomap` `fire` |
| `save_interval` | `600` | Seconds. Each save briefly stalls players |
| `backups` / `backup_short_interval` / `backup_long_interval` | `4` / `7200` / `43200` | Valheim's own rotation |
| `backup_enabled` / `backup_time` / `backup_retain` | `true` / `05:30` / `7` | The daily archive |
| `start_timeout` | `300` | Seconds to wait for a server after an update |

Modifiers: `combat` (`veryeasy` `easy` `hard` `veryhard`), `raids` (`none`
`muchless` `less` `more` `muchmore`), `deathpenalty` (`casual` `veryeasy` `easy`
`hard` `hardcore`), `resources` (`muchless` `less` `more` `muchmore` `most`),
`portals` (`casual` `hard` `veryhard`).

The port cannot be changed after creation.

## Where your data lives

- **On your computer:** `~/.valheim-deploy/` holds server records, passwords,
  agent tokens, SSH keys, your Hetzner token if you entered one, and downloaded
  backups. Keep it safe and back it up: without it you lose your way into cloud
  servers. It is outside the repo so that it cannot be committed.
- **On the server:** `/opt/valheim/instances/NAME/` (`data/` worlds, `backups/`).
  Backups on a server die with that server, so run `./valheim backups NAME` now and then.

The password reaches the game as a command-line argument, because Valheim accepts
it no other way, so any local user on the host can see it in `ps`. Do not use one
you use anywhere else.

## Troubleshooting

| Problem | Try |
|---|---|
| Players cannot connect | `./valheim doctor NAME`, then the firewall and port forward |
| Dashboard says it cannot reach a server | `./valheim doctor NAME`. For cloud servers, your IP may have changed: `./valheim allow-my-ip NAME` |
| "Incompatible version" when joining | `./valheim update NAME` |
| Install failed part way | Fix the cause shown, then `./valheim apply NAME`. Or `./valheim destroy NAME` to remove what was created |
| Player count shows nothing on a crossplay server | Expected: crossplay servers may not answer the query port. Status comes from the log |

## How it fits together

See [docs/design.md](docs/design.md). Run the tests with
`python3 -m unittest discover tests`.

## What has been tested

See [docs/design.md](docs/design.md#test-status) for exactly what was run for real
and what was only run against a fake.
