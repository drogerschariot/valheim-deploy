"""Server records, one JSON file each, under ~/.valheim-deploy.

Outside the repository on purpose: these hold passwords and agent tokens, and a
repository is a thing people push. Records are written after every cloud
resource is created, so an interrupted run can still be cleaned up by
`valheim destroy` rather than leaving something billing silently.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def home() -> Path:
    path = Path(os.environ.get("VALHEIM_DEPLOY_HOME", Path.home() / ".valheim-deploy"))
    for sub in ("", "servers", "keys", "backups"):
        (path / sub).mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def write_private(path: Path, content: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)


def path_for(name: str) -> Path:
    return home() / "servers" / f"{name}.json"


def save(rec: dict[str, Any]) -> None:
    write_private(path_for(rec["name"]), json.dumps(rec, indent=2))


def load(name: str) -> dict[str, Any]:
    path = path_for(name)
    if not path.exists():
        names = ", ".join(r["name"] for r in all_servers()) or "none yet"
        raise SystemExit(f"No server called {name!r}. Servers: {names}")
    return json.loads(path.read_text(encoding="utf-8"))


def all_servers() -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((home() / "servers").glob("*.json"))]


def remove(name: str) -> None:
    path_for(name).unlink(missing_ok=True)


def credential(key: str) -> str | None:
    path = home() / "credentials.json"
    return json.loads(path.read_text()).get(key) if path.exists() else None


def store_credential(key: str, value: str) -> None:
    path = home() / "credentials.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data[key] = value
    write_private(path, json.dumps(data, indent=2))
