"""AWS EC2, through the built-in client in awsapi.py, so all a user needs is an
access key. The wizard walks them through creating an account and a key.

An Elastic IP is attached so the join address survives a stop and start, and
everything is created one step at a time with the ID recorded straight away,
so a failure part way through can be undone by `valheim destroy`.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

from .. import registry, ssh
from . import Provider, my_public_ip, sizing
from .awsapi import AWSError, call, credentials, ec2, flatten, identity, items

# Canonical's account, which publishes the official Ubuntu images. Looked up
# with DescribeImages rather than the SSM parameter so the key needs EC2
# permissions only.
CANONICAL = "099720109477"
UBUNTU_IMAGES = "ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"
DISK_GB = 30
POLICY = "AmazonEC2FullAccess"
# Rough on-demand figures for us-east-1, running 24/7, for orientation only.
# Valheim's main thread is steady, which burstable t3 credits suit poorly at
# anything but light load, so the fixed-performance m6i is the default for 8 GB.
INSTANCE_TYPES = {
    4: [("t3.medium", "t3.medium: 2 vCPU (burstable), 4 GB, roughly $30/month + disk + IP")],
    8: [("m6i.large", "m6i.large: 2 vCPU, 8 GB, roughly $70/month + disk + IP"),
        ("t3.large", "t3.large: 2 vCPU (burstable), 8 GB, roughly $60/month + disk + IP")],
    16: [("m6i.xlarge", "m6i.xlarge: 4 vCPU, 16 GB, roughly $140/month + disk + IP")],
}

GUIDE = {
    "service": "AWS",
    "signup": [
        {"text": "Create an AWS account. You need an email address, a phone number and a payment card.",
         "link": "https://portal.aws.amazon.com/billing/signup"},
        {"text": "Activation can take a few minutes, sometimes longer. AWS emails you when the account is ready."},
        {"text": "Recommended: sign in and turn on multi-factor authentication for the account's root user.",
         "link": "https://console.aws.amazon.com/iam/home#/security_credentials"},
        {"text": "Recommended: create a monthly budget alert, so a forgotten server cannot surprise you.",
         "link": "https://console.aws.amazon.com/billing/home#/budgets"},
    ],
    "credentials": [
        {"text": "Open IAM and create a user called valheim-deploy. Leave console access off.",
         "link": "https://console.aws.amazon.com/iam/home#/users/create"},
        {"text": f"On the permissions page choose \"Attach policies directly\", tick {POLICY}, "
                 "then create the user."},
        {"text": "Open the new user, go to the Security credentials tab and choose \"Create access key\". "
                 "For the use case pick \"Local code\"."},
        {"text": "Copy the access key ID and the secret access key. AWS shows the secret only once."},
        {"text": "Paste both here. Do not use keys for the root user: this app refuses them."},
    ],
    "fields": [
        {"key": "aws_key_id", "label": "Access key ID", "secret": False},
        {"key": "aws_secret", "label": "Secret access key", "secret": True},
    ],
    "stored": "Kept only on this computer, in the app's credentials file. Delete the key in IAM to revoke it.",
}


def tags(rec: dict[str, Any], kind: str) -> dict[str, Any]:
    return flatten("TagSpecification", [{"ResourceType": kind, "Tag": [
        {"Key": "Name", "Value": f"valheim-{rec['name']}"},
        {"Key": "managed-by", "Value": "valheim-deploy"}]}])


def connect(fields: dict[str, str]) -> str:
    """Check a key, then keep it. Refuses root keys, and keys that cannot use EC2."""
    creds = {"key_id": fields.get("aws_key_id", "").strip(), "secret": fields.get("aws_secret", "").strip()}
    if not creds["key_id"] or not creds["secret"]:
        raise SystemExit("Enter both the access key ID and the secret access key.")
    try:
        who = identity(creds)
    except AWSError as exc:
        raise SystemExit("AWS did not accept that key. Check you copied both parts, and that the "
                         "account has finished activating.") from exc
    if who.get("Arn", "").endswith(":root"):
        raise SystemExit("Those are the root user's keys. Create an IAM user as the steps show, "
                         "and delete the root key in IAM.")
    try:
        call("ec2", "us-east-1", "DescribeRegions", creds=creds)
    except AWSError as exc:
        raise SystemExit(f"The key works, but it cannot use EC2. Attach the {POLICY} policy to "
                         "the user, then connect again.") from exc
    registry.store_credential("aws", creds)
    return describe(who)


def describe(who: dict[str, str]) -> str:
    user = who.get("Arn", "").rsplit("/", 1)[-1]
    return f"AWS account {who.get('Account')}, user {user}"


def wait_for(region: str, instance_id: str, state: str, limit: int) -> dict[str, Any]:
    deadline = time.time() + limit
    while True:
        found = ec2(region, "DescribeInstances", {"InstanceId.1": instance_id})
        instances = [i for r in items(found.get("reservationSet")) for i in items(r.get("instancesSet"))]
        if instances and instances[0]["instanceState"]["name"] == state:
            return instances[0]
        if time.time() > deadline:
            raise SystemExit(f"The instance did not reach {state} within {limit // 60} minutes.")
        time.sleep(5)


class AWS(Provider):
    key = "aws"
    label = "Amazon Web Services (EC2)"
    guide = GUIDE

    def connected(self) -> str | None:
        found = credentials()
        if not found:
            return None
        try:
            return f"{describe(identity(found[0]))} (key {found[1]})"
        except AWSError:
            return None

    def connect(self, fields: dict[str, str]) -> str:
        return connect(fields)

    def wizard_options(self, params: dict[str, str]) -> dict[str, Any]:
        account = self.connected()
        if not account:
            return {"needs_connect": True, "guide": GUIDE}
        want = sizing(int(params.get("players") or 4))
        regions = sorted(r["regionName"] for r in items(ec2("us-east-1", "DescribeRegions").get("regionInfo")))
        return {"needs_connect": False, "connected": account, "guide": GUIDE,
                "recommended_gb": want, "default_region": "us-east-1",
                "regions": [{"value": r, "label": r} for r in regions],
                "instance_types": [{"value": v, "label": l} for v, l in INSTANCE_TYPES[want]]}

    def collect(self, ui: Any, rec: dict[str, Any]) -> None:
        account = self.walk_through(ui)
        ui.ok(f"Using {account}.")
        region = ui.ask("aws_region", "AWS region (closest to your players)", "us-east-1")
        rec["resources"] = {"region": region}
        ec2(region, "DescribeAvailabilityZones")  # a bad or disabled region fails here, not later
        want = sizing(rec["players"])
        rec["resources"]["instance_type"] = ui.choose(
            "aws_instance_type", f"Which instance? {want} GB recommended.", INSTANCE_TYPES[want])

    def describe_cost(self, rec: dict[str, Any]) -> str:
        return (f"AWS bills for the {rec['resources']['instance_type']} instance, a {DISK_GB} GB disk, "
                "a public IP and outbound data until the server is deleted.")

    def create(self, ui: Any, rec: dict[str, Any]) -> None:
        res, name = rec["resources"], rec["name"]
        region, port = res["region"], rec["settings"]["port"]
        key = ssh.generate_key(name)

        ui.say("Creating key pair, security group, instance and address at AWS...")
        public = Path(f"{key}.pub").read_bytes()
        ec2(region, "ImportKeyPair", {"KeyName": f"valheim-{name}",
                                      "PublicKeyMaterial": base64.b64encode(public).decode(),
                                      **tags(rec, "key-pair")})
        res["key_name"] = f"valheim-{name}"
        registry.save(rec)

        vpcs = items(ec2(region, "DescribeVpcs", flatten("Filter", [
            {"Name": "isDefault", "Value": ["true"]}])).get("vpcSet"))
        if not vpcs:
            raise SystemExit(f"Region {region} has no default VPC. Create one in the VPC console "
                             "(Actions > Create default VPC), delete this server, and start over.")
        res["security_group_id"] = ec2(region, "CreateSecurityGroup", {
            "GroupName": f"valheim-{name}", "GroupDescription": f"Valheim server {name}",
            "VpcId": vpcs[0]["vpcId"], **tags(rec, "security-group")})["groupId"]
        registry.save(rec)

        self._ssh_rule(rec, "AuthorizeSecurityGroupIngress", f"{my_public_ip()}/32")
        ec2(region, "AuthorizeSecurityGroupIngress", {"GroupId": res["security_group_id"], **flatten(
            "IpPermissions", [{"IpProtocol": "udp", "FromPort": port, "ToPort": port + 1,
                               "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "Valheim"}],
                               "Ipv6Ranges": [{"CidrIpv6": "::/0", "Description": "Valheim"}]}])})

        images = items(ec2(region, "DescribeImages", {"Owner.1": CANONICAL, **flatten("Filter", [
            {"Name": "name", "Value": [UBUNTU_IMAGES]},
            {"Name": "architecture", "Value": ["x86_64"]},
            {"Name": "state", "Value": ["available"]}])}).get("imagesSet"))
        if not images:
            raise SystemExit(f"No Ubuntu 24.04 image was found in {region}.")
        ami = max(images, key=lambda i: i["creationDate"])["imageId"]

        launched = ec2(region, "RunInstances", {
            "ImageId": ami, "InstanceType": res["instance_type"], "KeyName": res["key_name"],
            "SecurityGroupId.1": res["security_group_id"], "MinCount": 1, "MaxCount": 1,
            **flatten("BlockDeviceMapping", [{"DeviceName": "/dev/sda1", "Ebs": {
                "VolumeSize": DISK_GB, "VolumeType": "gp3", "DeleteOnTermination": True}}]),
            "MetadataOptions.HttpTokens": "required", **tags(rec, "instance")})
        res["instance_id"] = items(launched.get("instancesSet"))[0]["instanceId"]
        registry.save(rec)

        wait_for(region, res["instance_id"], "running", 300)
        address = ec2(region, "AllocateAddress", {"Domain": "vpc", **tags(rec, "elastic-ip")})
        res["allocation_id"] = address["allocationId"]
        registry.save(rec)
        ec2(region, "AssociateAddress", {"InstanceId": res["instance_id"],
                                         "AllocationId": res["allocation_id"]})

        rec["address"] = address["publicIp"]
        rec["ssh"] = {"host": rec["address"], "user": "ubuntu", "port": 22, "key": key}
        registry.save(rec)

    def _ssh_rule(self, rec: dict[str, Any], action: str, cidr: str) -> None:
        ec2(rec["resources"]["region"], action, {
            "GroupId": rec["resources"]["security_group_id"], **flatten("IpPermissions", [
                {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
                 "IpRanges": [{"CidrIp": cidr, "Description": "SSH, owner only"}]}])})

    def allow_ip(self, ui: Any, rec: dict[str, Any], ip: str) -> None:
        group = items(ec2(rec["resources"]["region"], "DescribeSecurityGroups", {
            "GroupId.1": rec["resources"]["security_group_id"]}).get("securityGroupInfo"))[0]
        for perm in items(group.get("ipPermissions")):
            if perm.get("fromPort") == "22":
                for item in items(perm.get("ipRanges")):
                    self._ssh_rule(rec, "RevokeSecurityGroupIngress", item["cidrIp"])
        self._ssh_rule(rec, "AuthorizeSecurityGroupIngress", f"{ip}/32")

    def destroy(self, ui: Any, rec: dict[str, Any]) -> None:
        res = rec.get("resources", {})
        region = res.get("region")
        if res.get("instance_id"):
            ui.say("Terminating the instance (this can take a minute)...")
            ec2(region, "TerminateInstances", {"InstanceId.1": res["instance_id"]})
            wait_for(region, res["instance_id"], "terminated", 600)
        if res.get("allocation_id"):
            ec2(region, "ReleaseAddress", {"AllocationId": res["allocation_id"]})
        if res.get("security_group_id"):
            ec2(region, "DeleteSecurityGroup", {"GroupId": res["security_group_id"]})
        if res.get("key_name"):
            ec2(region, "DeleteKeyPair", {"KeyName": res["key_name"]})
