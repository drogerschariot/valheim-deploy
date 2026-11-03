# Getting started

Open the app, and the setup page opens in your browser. These are the things to
have ready for each place you can host. If you get stuck on one, try Hetzner: it
needs the least setup.

## Hetzner Cloud (recommended)

You need a Hetzner account with a payment method.

1. Sign in at [console.hetzner.cloud](https://console.hetzner.cloud) and open or create a project.
2. Go to **Security > API tokens** and generate a token with **Read & Write** access.
3. In the app, choose **Hetzner Cloud** and paste the token when asked.
4. Pick the location closest to your players and a plan. Prices shown are live.

The firewall is done for you. Billing runs until you press **Delete server**;
stopping the game does not stop it.

## AWS

1. Install the [AWS CLI](https://aws.amazon.com/cli/).
2. In a terminal, run `aws configure` and enter your access keys, or sign in with `aws sso login`.
3. Restart the app, then choose **Amazon Web Services**.

The account needs permission to manage EC2 and read SSM parameters, and the region
needs a default VPC. The firewall is done for you. AWS usually costs noticeably
more than Hetzner for a server that runs all the time.

## A Linux server you already have

You need:

- x86-64 Ubuntu 20.04 or newer. ARM machines cannot run the Valheim server.
- At least 4 GB of RAM and 10 GB of free disk.
- SSH access with a key, as root or as a user with passwordless sudo.

In the app, enter its address and key path and press **Test the login**.

Then open the game ports yourself. See [port forwarding](#port-forwarding).

The option to let the installer manage ufw blocks all other inbound traffic except
SSH and the game ports. Leave it off if this machine runs anything else.

## This computer

Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start
it, then choose **This computer**. The server stops when the computer sleeps or
shuts down, so this suits trying it out or a LAN party.

Then open the game ports yourself. See [port forwarding](#port-forwarding).

On Apple Silicon and other ARM computers the server runs under emulation, which is
likely to be slow and unreliable.

## Port forwarding

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
