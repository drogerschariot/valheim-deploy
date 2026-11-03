"""AWS EC2, driven through the aws CLI so credentials work however the user has
them set up (profiles, SSO, env vars) and this repo needs no SDK.

An Elastic IP is attached so the join address survives a stop and start, and
everything is created one step at a time with the ID recorded straight away,
so a failure part way through can be undone by `valheim destroy`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .. import registry, ssh
from . import Provider, my_public_ip, sizing

AMI_PARAMETER = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
DISK_GB = 30
# Rough on-demand figures for us-east-1, running 24/7, for orientation only.
# Valheim's main thread is steady, which burstable t3 credits suit poorly at
# anything but light load, so the fixed-performance m6i is the default for 8 GB.
INSTANCE_TYPES = {
    4: [("t3.medium", "t3.medium: 2 vCPU (burstable), 4 GB, roughly $30/month + disk + IP")],
    8: [("m6i.large", "m6i.large: 2 vCPU, 8 GB, roughly $70/month + disk + IP"),
        ("t3.large", "t3.large: 2 vCPU (burstable), 8 GB, roughly $60/month + disk + IP")],
    16: [("m6i.xlarge", "m6i.xlarge: 4 vCPU, 16 GB, roughly $140/month + disk + IP")],
}


def aws(rec: dict[str, Any], *args: str) -> Any:
    region = rec["resources"]["region"]
    # Resolved first: Windows will not launch a pip-installed aws.cmd by bare name.
    result = subprocess.run([shutil.which("aws") or "aws", "--region", region, "--output", "json", *args],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"AWS refused `{' '.join(args[:2])}`: {result.stderr.strip()}")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def tags(rec: dict[str, Any], kind: str) -> str:
    return (f"ResourceType={kind},Tags=[{{Key=Name,Value=valheim-{rec['name']}}},"
            "{Key=managed-by,Value=valheim-deploy}]")


class AWS(Provider):
    key = "aws"
    label = "Amazon Web Services (EC2)"

    def preflight(self) -> str | None:
        if not shutil.which("aws"):
            return "The AWS CLI is needed: https://aws.amazon.com/cli/ then run `aws configure`."
        return super().preflight()

    def wizard_options(self, params: dict[str, str]) -> dict[str, Any]:
        want = sizing(int(params.get("players") or 4))
        return {"recommended_gb": want, "default_region": "us-east-1",
                "instance_types": [{"value": v, "label": l} for v, l in INSTANCE_TYPES[want]]}

    def collect(self, ui: Any, rec: dict[str, Any]) -> None:
        region = ui.ask("aws_region", "AWS region (closest to your players)", "us-east-1")
        rec["resources"] = {"region": region}
        who = aws(rec, "sts", "get-caller-identity")
        ui.ok(f"Using AWS account {who['Account']}.")
        want = sizing(rec["players"])
        rec["resources"]["instance_type"] = ui.choose(
            "aws_instance_type", f"Which instance? {want} GB recommended.", INSTANCE_TYPES[want])

    def describe_cost(self, rec: dict[str, Any]) -> str:
        return (f"AWS bills for the {rec['resources']['instance_type']} instance, a {DISK_GB} GB disk, "
                "a public IP and outbound data until the server is deleted.")

    def create(self, ui: Any, rec: dict[str, Any]) -> None:
        res, name = rec["resources"], rec["name"]
        port = rec["settings"]["port"]
        key = ssh.generate_key(name)

        ui.say("Creating key pair, security group, instance and address at AWS...")
        aws(rec, "ec2", "import-key-pair", "--key-name", f"valheim-{name}",
            "--public-key-material", f"fileb://{Path(key).as_posix()}.pub")
        res["key_name"] = f"valheim-{name}"
        registry.save(rec)

        vpcs = aws(rec, "ec2", "describe-vpcs", "--filters", "Name=isDefault,Values=true")["Vpcs"]
        if not vpcs:
            raise SystemExit(f"Region {res['region']} has no default VPC. Create one with "
                             "`aws ec2 create-default-vpc`, delete this server, and start over.")
        res["security_group_id"] = aws(
            rec, "ec2", "create-security-group", "--group-name", f"valheim-{name}",
            "--description", f"Valheim server {name}", "--vpc-id", vpcs[0]["VpcId"],
            "--tag-specifications", tags(rec, "security-group"))["GroupId"]
        registry.save(rec)

        self._ssh_rule(rec, "authorize-security-group-ingress", f"{my_public_ip()}/32")
        aws(rec, "ec2", "authorize-security-group-ingress", "--group-id", res["security_group_id"],
            "--ip-permissions", json.dumps([{
                "IpProtocol": "udp", "FromPort": port, "ToPort": port + 1,
                "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "Valheim"}],
                "Ipv6Ranges": [{"CidrIpv6": "::/0", "Description": "Valheim"}],
            }]))

        ami = aws(rec, "ssm", "get-parameter", "--name", AMI_PARAMETER)["Parameter"]["Value"]
        instance = aws(
            rec, "ec2", "run-instances", "--image-id", ami, "--instance-type", res["instance_type"],
            "--key-name", res["key_name"], "--security-group-ids", res["security_group_id"],
            "--count", "1",
            "--block-device-mappings", json.dumps([{"DeviceName": "/dev/sda1", "Ebs": {
                "VolumeSize": DISK_GB, "VolumeType": "gp3", "DeleteOnTermination": True}}]),
            "--metadata-options", "HttpTokens=required",
            "--tag-specifications", tags(rec, "instance"))["Instances"][0]
        res["instance_id"] = instance["InstanceId"]
        registry.save(rec)

        aws(rec, "ec2", "wait", "instance-running", "--instance-ids", res["instance_id"])
        address = aws(rec, "ec2", "allocate-address", "--domain", "vpc",
                      "--tag-specifications", tags(rec, "elastic-ip"))
        res["allocation_id"] = address["AllocationId"]
        registry.save(rec)
        aws(rec, "ec2", "associate-address", "--instance-id", res["instance_id"],
            "--allocation-id", res["allocation_id"])

        rec["address"] = address["PublicIp"]
        rec["ssh"] = {"host": rec["address"], "user": "ubuntu", "port": 22, "key": key}
        registry.save(rec)

    def _ssh_rule(self, rec: dict[str, Any], verb: str, cidr: str) -> None:
        aws(rec, "ec2", verb, "--group-id", rec["resources"]["security_group_id"],
            "--ip-permissions", json.dumps([{
                "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
                "IpRanges": [{"CidrIp": cidr, "Description": "SSH, owner only"}],
            }]))

    def allow_ip(self, ui: Any, rec: dict[str, Any], ip: str) -> None:
        group = aws(rec, "ec2", "describe-security-groups", "--group-ids",
                    rec["resources"]["security_group_id"])["SecurityGroups"][0]
        for perm in group["IpPermissions"]:
            if perm.get("FromPort") == 22:
                for item in perm.get("IpRanges", []):
                    self._ssh_rule(rec, "revoke-security-group-ingress", item["CidrIp"])
        self._ssh_rule(rec, "authorize-security-group-ingress", f"{ip}/32")

    def destroy(self, ui: Any, rec: dict[str, Any]) -> None:
        res = rec.get("resources", {})
        if res.get("instance_id"):
            ui.say("Terminating the instance (this can take a minute)...")
            aws(rec, "ec2", "terminate-instances", "--instance-ids", res["instance_id"])
            aws(rec, "ec2", "wait", "instance-terminated", "--instance-ids", res["instance_id"])
        if res.get("allocation_id"):
            aws(rec, "ec2", "release-address", "--allocation-id", res["allocation_id"])
        if res.get("security_group_id"):
            aws(rec, "ec2", "delete-security-group", "--group-id", res["security_group_id"])
        if res.get("key_name"):
            aws(rec, "ec2", "delete-key-pair", "--key-name", res["key_name"])
