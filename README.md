# Valheim Deploy

Run your own Valheim dedicated servers on your Windows PC, as many as you like,
from one dashboard.

**[Download Valheim Deploy for Windows](https://drogerschariot.github.io/valheim-deploy/)**

## Get started

1. Download **ValheimDeploy-Setup.exe** from the
   [download page](https://drogerschariot.github.io/valheim-deploy/) and run it.
   It installs for you only, with no admin prompt. Windows 10 or 11, 64-bit.
2. The app isn't code-signed yet, so Windows may say **"Windows protected your PC"**:
   choose **More info**, then **Run anyway**.
3. Double-click **Valheim Deploy** on your desktop. The dashboard opens.
4. Create a server. The app downloads the Valheim server from Steam (about 1 GB,
   once) and starts it.
5. Work through the **Network** tab, then send friends the join address and
   password. [Getting started](docs/getting-started.md) has the detail.

> **This is an advanced setup.** For friends outside your home to join, you need
> to be comfortable signing in to your router and forwarding ports, allowing the
> game through Windows Firewall, and knowing your public IP address. The Network
> tab walks you through each and does what it can for you. Some internet
> providers use CGNAT, which makes home hosting impossible. If that sounds like a
> lot, a paid Valheim host will be a much better experience. Setup says this too,
> and won't continue until you accept it.

## Using it

- **Opening the app** shows the dashboard and starts every server that has
  **Start when the app opens** ticked. That's on for new servers.
- **Closing the window** leaves your servers running. The app stays in the
  system tray, by the clock. Double-click the desktop icon or the tray icon to
  open the dashboard again.
- **Quit**, in the tray menu or the dashboard, stops every server and saves its
  world first. That can take up to two minutes. Shutting down or restarting
  Windows without quitting first can lose progress since the last autosave,
  which happens every 10 minutes.
- **While a server is running**, the app keeps your PC from going to sleep.

Each server has Start, Stop and Restart buttons, live status, players and logs,
admin, ban and permit lists, daily backups, and **Update game**.

When Valheim updates, players can't join until the server matches, so press
**Update game**. It stops your running servers, backs them up, updates, and starts
them again.

To join, in Valheim choose **Join Game > Add server** and enter the join address.
Crossplay servers use a join code instead, shown on the Connect tab.

## The Network tab

Home hosting fails in a few predictable ways. The Network tab checks each one and
fixes what it can:

| Check | What the app does |
|---|---|
| Windows Firewall | Adds a rule for the game's UDP ports, for the Valheim server only. Windows asks for permission once |
| Router port forwarding | Asks your router to forward the ports (UPnP). If your router doesn't allow that, it shows the manual steps with your own addresses filled in |
| CGNAT and double NAT | Compares your router's internet address with your public IP, and says what it means if they differ |
| Public IP changes | Warns when your public IP has changed, since friends then need the new address |
| What to send friends | The internet and home-network join addresses |

Port forwarding needs **UDP** only, on two ports per server: 2456-2457 for your
first server, and 2466-2467 for the second. No TCP port is needed, and the
dashboard is never reachable from outside your PC.

**Check from the internet** on the Connect tab can fail from inside your own
network even when outside players connect fine, because many routers don't loop
traffic back. Ask a friend to try.

## Where your data lives

`%LOCALAPPDATA%\ValheimDeploy` holds your servers, worlds
(`instances\NAME\data`), backups (`instances\NAME\backups`), and the shared game
install. Uninstalling asks whether to delete it, and keeps it if you say no.

The join password reaches the game as a command-line argument, because Valheim
accepts it no other way, so other programs on your PC can see it. Don't use a
password you use anywhere else.

## Troubleshooting

| Problem | Try |
|---|---|
| "Windows protected your PC" | Expected while the app is unsigned: **More info > Run anyway** |
| Friends can't connect | Work down the Network tab. If it says CGNAT, ask your internet provider for a public IPv4 address |
| Friends could connect, now can't | Your public IP may have changed: the Network tab shows the new join address |
| "Incompatible version" when joining | Press **Update game** |
| The server keeps restarting | See its Logs tab. Something else may be using its ports |
| Antivirus quarantined the app | New unsigned apps are sometimes flagged. Restore it, or allow it in your antivirus |

## Command-line tool and other hosts

The repo also has a command-line tool that creates servers on Hetzner Cloud, AWS,
any Linux server over SSH, or in Docker, with the same dashboard. It isn't part of
the Windows app. Run it from source:

```sh
git clone https://github.com/drogerschariot/valheim-deploy.git && cd valheim-deploy
./valheim            # needs Python 3.12+; opens the dashboard
./valheim new        # create a server in the terminal
./valheim --help
```

[Getting started](docs/getting-started.md#other-hosts-command-line) covers
those hosts.

## For developers

```sh
python3 -m unittest discover tests
python -m pip install -r packaging/requirements-windows.txt
python packaging/build.py windows-app      # on Windows: builds dist\ValheimDeploy\
iscc /DAppVersion=0.5.0 packaging\installer.iss   # Inno Setup 6: dist\ValheimDeploy-Setup.exe
```

[docs/design.md](docs/design.md) explains how it fits together. Its
[test status](docs/design.md#test-status) says exactly what has run for real and
what has only run against a fake.
