# Getting started

Open the app, and the setup page opens in your browser. It asks where to host,
and for Hetzner, AWS or a rented server it asks whether you already have an
account. If you don't, it walks you through making one, then through getting the
key or token it needs, with links to each page. This page is the same steps, to
read ahead. If you get stuck on one, try Hetzner: it needs the least setup.

Keys and tokens you paste are checked with the provider straight away, and kept
only on your computer, in `credentials.json` in the app's folder
(`~/.valheim-deploy`, or `%USERPROFILE%\.valheim-deploy` on Windows). Delete the
key or token at the provider to revoke it.

## Hetzner Cloud (recommended)

1. [Create a Hetzner account](https://accounts.hetzner.com/signUp). Hetzner may ask
   for a payment method or proof of identity before you can create servers.
2. In the [Cloud Console](https://console.hetzner.cloud), create a project.
3. In the project, go to **Security > API tokens** and generate a token with
   **Read & Write** access. Hetzner shows it only once.
4. In the app, choose **Hetzner Cloud**, paste the token and press **Connect**.
5. Pick the location closest to your players and a plan. Prices shown are live.

The firewall is done for you. Billing runs until you press **Delete server**;
stopping the game does not stop it.

## AWS

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

## A Linux server you already have, or rent

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
