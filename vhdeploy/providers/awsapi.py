"""A small AWS client: Signature Version 4 over the EC2 and STS query APIs.

Built in, rather than calling the aws CLI, so that a user only has to paste an
access key into the wizard. Installing and configuring a CLI is too much to ask
of someone who wants a game server, and impossible from a browser. Standard
library only, like the rest of the app.
"""

from __future__ import annotations

import configparser
import datetime
import hashlib
import hmac
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .. import registry

EC2_VERSION = "2016-11-15"
STS_VERSION = "2011-06-15"


class AWSError(SystemExit):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"AWS refused: {code}: {message}")
        self.aws_code = code


# -- credentials -------------------------------------------------------------

def credentials() -> tuple[dict[str, str], str] | None:
    """(keys, where they came from). Keys saved by the wizard win over the
    environment and ~/.aws, because the wizard is where the user chose them."""
    stored = registry.credential("aws")
    if stored:
        return stored, "saved in this app"
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        return {"key_id": os.environ["AWS_ACCESS_KEY_ID"], "secret": os.environ["AWS_SECRET_ACCESS_KEY"],
                "session_token": os.environ.get("AWS_SESSION_TOKEN", "")}, "environment variables"
    path = Path(os.environ.get("AWS_SHARED_CREDENTIALS_FILE", Path.home() / ".aws" / "credentials"))
    if path.exists():
        parser = configparser.ConfigParser()
        parser.read(path)
        profile = os.environ.get("AWS_PROFILE", "default")
        if parser.has_option(profile, "aws_access_key_id"):
            section = parser[profile]
            return {"key_id": section["aws_access_key_id"], "secret": section.get("aws_secret_access_key", ""),
                    "session_token": section.get("aws_session_token", "")}, f"{path} [{profile}]"
    return None


def require_credentials() -> dict[str, str]:
    found = credentials()
    if not found:
        raise SystemExit("No AWS access key. Connect your AWS account in the setup wizard first.")
    return found[0]


# -- signing -----------------------------------------------------------------

def _hmac(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode(), hashlib.sha256).digest()


def sign(method: str, url: str, headers: dict[str, str], body: bytes, region: str, service: str,
         key_id: str, secret: str, amz_date: str) -> str:
    """The Authorization header for one request. headers must already hold
    every header to be signed, including host and x-amz-date."""
    parts = urllib.parse.urlsplit(url)
    query = "&".join(sorted(
        f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)))
    lower = {k.lower(): " ".join(v.strip().split()) for k, v in headers.items()}
    names = sorted(lower)
    canonical = "\n".join([
        method, urllib.parse.quote(parts.path or "/", safe="/-_.~"), query,
        "".join(f"{n}:{lower[n]}\n" for n in names), ";".join(names),
        hashlib.sha256(body).hexdigest()])
    scope = f"{amz_date[:8]}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = _hmac(f"AWS4{secret}".encode(), amz_date[:8])
    for part in (region, service, "aws4_request"):
        key = _hmac(key, part)
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return (f"AWS4-HMAC-SHA256 Credential={key_id}/{scope}, "
            f"SignedHeaders={';'.join(names)}, Signature={signature}")


# -- responses ---------------------------------------------------------------

def _tag(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _plain(node: ET.Element) -> Any:
    """XML to dicts and lists. EC2 marks list entries with <item>."""
    kids = list(node)
    if not kids:
        return node.text or ""
    if all(_tag(k) == "item" for k in kids):
        return [_plain(k) for k in kids]
    return {_tag(k): _plain(k) for k in kids}


def items(value: Any) -> list[Any]:
    """A list from a response field. An empty list arrives as an empty tag."""
    return value if isinstance(value, list) else []


def _error(raw: bytes) -> tuple[str, str]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return "Error", raw.decode(errors="replace")[:300]
    found = {_tag(n): n.text or "" for n in root.iter() if _tag(n) in ("Code", "Message")}
    return found.get("Code", "Error"), found.get("Message", "")


# -- calls -------------------------------------------------------------------

def endpoint(service: str, region: str) -> str:
    override = os.environ.get("AWS_ENDPOINT_URL")
    return override.rstrip("/") + "/" if override else f"https://{service}.{region}.amazonaws.com/"


def call(service: str, region: str, action: str, params: dict[str, Any] | None = None,
         creds: dict[str, str] | None = None) -> dict[str, Any]:
    creds = creds or require_credentials()
    version = EC2_VERSION if service == "ec2" else STS_VERSION
    fields = {"Action": action, "Version": version,
              **{k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in (params or {}).items()}}
    body = urllib.parse.urlencode(fields, quote_via=urllib.parse.quote).encode()
    url = endpoint(service, region)
    amz_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    headers = {"Host": urllib.parse.urlsplit(url).netloc, "X-Amz-Date": amz_date,
               "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}
    if creds.get("session_token"):
        headers["X-Amz-Security-Token"] = creds["session_token"]
    headers["Authorization"] = sign("POST", url, headers, body, region, service,
                                    creds["key_id"], creds["secret"], amz_date)
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as res:
            root = ET.fromstring(res.read())
    except urllib.error.HTTPError as exc:
        raise AWSError(*_error(exc.read())) from exc
    result = _plain(root)
    # STS wraps the answer in <ActionResult>; EC2 does not.
    return result.get(f"{action}Result", result) if isinstance(result, dict) else {}


def ec2(region: str, action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return call("ec2", region, action, params)


def flatten(prefix: str, value: Any, out: dict[str, Any] | None = None) -> dict[str, Any]:
    """Nested lists and dicts to EC2's numbered parameters:
    {"IpPermissions": [{"IpRanges": [{"CidrIp": x}]}]} becomes
    IpPermissions.1.IpRanges.1.CidrIp=x."""
    out = {} if out is None else out
    if isinstance(value, dict):
        for k, v in value.items():
            flatten(f"{prefix}.{k}" if prefix else k, v, out)
    elif isinstance(value, list):
        for i, v in enumerate(value, 1):
            flatten(f"{prefix}.{i}", v, out)
    else:
        out[prefix] = value
    return out


def identity(creds: dict[str, str]) -> dict[str, str]:
    return call("sts", "us-east-1", "GetCallerIdentity", creds=creds)
