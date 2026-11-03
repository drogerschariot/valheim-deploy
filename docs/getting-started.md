# Getting started

## On your Windows PC

### What you need

- Windows 10 or 11, 64-bit, with about 3 GB of free disk for the game and worlds.
- At least 8 GB of RAM if you play on the same PC. Each server uses 2-4 GB, more
  as the world is explored.
- Access to your router's settings, to forward ports, unless the app can do it
  for you through UPnP.
- An internet connection with its own public IPv4 address, not CGNAT. The Network
  tab checks this for you.

### First server

1. Install and open the app (see the [README](../README.md#get-started)).
2. Read and accept the advanced-setup warning.
3. Choose a short name, the server name players see, a world name and a password.
   The password must be at least 5 characters, and not part of the server name.
4. Press **Create server**. The first time, the app downloads SteamCMD and the
   Valheim server, about 1 GB. Later servers share that install and start straight
   away.
5. When it's ready, the dashboard opens the **Network** tab.

### The Network tab, step by step

**1. Windows Firewall.** Press **Add firewall rule** and choose **Yes** when
Windows asks for permission. The rule allows only the server's two UDP ports, and
only for `valheim_server.exe`. If you use another firewall or antivirus suite, allow
the same ports there too.

**2. Router port forwarding.** Press **Open ports automatically**. The app asks
your router, over UPnP, to send the game ports to this PC. If the router doesn't
answer, UPnP is probably off; you can turn it on in the router, or forward by hand:

1. Open your router's page. The tab shows its address, often `http://192.168.1.1`
   or `http://192.168.0.1`. The sign-in is often printed on the router.
2. Find **Port Forwarding**. Routers also call it Virtual Server, NAT, Applications
   or Gaming.
3. Add a rule: protocol **UDP**, ports **2456-2457** (the tab shows each server's
   own ports), to this PC's LAN address, which the tab also shows.
4. Reserve that LAN address for this PC, under DHCP or LAN settings, so it doesn't
   change and break the rule.

**3. Your internet connection.** The tab compares your router's internet address
with your public IP:

- **Same:** port forwarding works.
- **Your provider uses CGNAT:** the router's address starts with 100.64 to
  100.127, or doesn't match your public IP. Your provider shares one public
  address between customers, so no setting at home can let friends in. Ask the
  provider for a public IPv4 address; many give one free or for a small fee.
  Players on your own network can still join.
- **A second router in front:** often the provider's modem-router. Forward the
  ports on it too, to your own router's address, or put it in bridge mode.

**4. What to send friends.** The internet join address (`public IP:port`) and the
password, from the Connect tab. Players in your home use the home-network address
instead.

Then ask a friend outside your home to join. That's the real test: checking from
inside your own network can fail even when the setup is right.

### Day to day

- **Opening the app** starts servers that have **Start when the app opens** ticked.
- **Closing the window** leaves them running in the system tray.
- **Quit**, in the tray or the dashboard, stops them and saves each world.
- **Update game**, after Valheim updates, so players can join again.
- **Your public IP can change**, often when the router restarts. The Network tab
  warns you, and shows the new join address to send. A dynamic DNS name, which
  many routers offer, gives friends an address that never changes.

## Other hosts (command line)

These use the command-line tool from the repo, not the Windows app. See the
[README](../README.md#command-line-tool-and-other-hosts).

The command-line wizard asks where to host, and for Hetzner, AWS or a rented
server it asks whether you already have an account. If you don't, it walks you
through making one, then through getting the key or token it needs. If you get
stuck on one, try Hetzner: it needs the least setup.

Keys and tokens you paste are checked with the provider straight away, and kept
only on your computer, in `credentials.json` in `~/.valheim-deploy`. Delete the
key or token at the provider to revoke it.

### Hetzner Cloud (recommended)

1. [Create a Hetzner account](https://accounts.hetzner.com/signUp). Hetzner may ask
   for a payment method or proof of identity before you can create servers.
2. In the [Cloud Console](https://console.hetzner.cloud), create a project.
3. In the project, go to **Security > API tokens** and generate a token with
   **Read & Write** access. Hetzner shows it only once.
4. In the app, choose **Hetzner Cloud**, paste the token and press **Connect**.
5. Pick the location closest to your players and a plan. Prices shown are live.

The firewall is done for you. Billing runs until you press **Delete server**;
stopping the game does not stop it.

### AWS

No AWS tools need installing.

1. [Create an AWS account](https://portal.aws.amazon.com/billing/signup). It needs an
   email address, a phone number and a payment card, and activation can take a while.
2. Recommended: turn on multi-factor authentication for the root user, and set a
   [budget alert](https://console.aws.amazon.com/billing/home#/budgets).
3. In IAM, [create a user](https://console.aws.amazon.com/iam/home#/users/create)
   called `valheim-deploy` without console access. Choose **Attach policies
   directly** and tick **AmazonEC2FullAccess**.
4. Open the user, then **Security credentials > Create access key**, with the use
   case **Local code**. Copy the access key ID and the secret. AWS shows the secret
   only once.
5. In the app, choose **Amazon Web Services**, paste both and press **Connect**.

The app refuses the root user's keys, and keys that cannot use EC2. If you set
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`, or have a `default` profile in
`~/.aws/credentials`, the app uses those until you connect a key in the wizard.
AWS SSO logins are not supported.

The region needs a default VPC, which new accounts have. The firewall is done for
you. AWS usually costs noticeably more than Hetzner for a server that runs all the
time.

### A Linux server you already have, or rent

For a VPS from another provider, such as DigitalOcean, Akamai Linode, Vultr or
OVHcloud, answer **No, help me rent one**. The app lists what to choose and makes
an SSH key for you to paste into the provider's form.

You need:

- x86-64 Ubuntu 20.04 or newer. ARM machines cannot run the Valheim server.
- At least 4 GB of RAM and 10 GB of free disk.
- SSH access with a key, as root or as a user with passwordless sudo.

In the app, enter its address and key path and press **Test the login**.

Then open the game ports yourself. See [port forwarding](#port-forwarding).

The option to let the installer manage ufw blocks all other inbound traffic except
SSH and the game ports. Leave it off if this machine runs anything else.

### This computer

Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start
it, then choose **This computer**. The server stops when the computer sleeps or
shuts down, so this suits trying it out or a LAN party.

Then open the game ports yourself. See [port forwarding](#port-forwarding).

On Apple Silicon and other ARM computers the server runs under emulation, which is
likely to be slow and unreliable.

### Port forwarding

Needed for a server at home. Cloud servers do not need this.

1. Find the machine's LAN address, such as `192.168.1.50`, and reserve it in your
   router's DHCP settings so it does not change.
2. In your router, forward **UDP** ports to that address. Your first server uses
   2456-2457. The Connect tab shows the exact ports for each server.
3. Allow the same UDP ports through the machine's own firewall. On Windows, Docker
   Desktop usually prompts for this.
4. Give players your public IP address and the port, which the Connect tab shows.

Things that catch people out:

- If your provider uses **CGNAT**, port forwarding cannot work. Ask them for a
  public IP address, or use a cloud server.
- Your public IP can change, and then the join address changes with it.
- Do not forward any TCP port. The dashboard does not need one.
- **Check from the internet** can fail from inside your own network even when
  outside players connect fine, so ask a friend to try joining.
