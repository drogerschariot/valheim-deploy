#!/usr/bin/env python3
"""Container entrypoint: lay down /etc/valheim, fix ownership, drop to the
service account, and hand over to the agent, which supervises the game."""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path


def main() -> None:
    cfg = json.loads(os.environ["VALHEIM_CONFIG_JSON"])
    name = cfg["name"]
    Path(f"/etc/valheim/{name}.json").write_text(json.dumps(cfg), encoding="utf-8")
    Path(f"/etc/valheim/{name}.conf").write_text(
        f"INSTANCE_DIR={shlex.quote(cfg['instance_dir'])}\n"
        f"WORLD_NAME={shlex.quote(cfg['world_name'])}\n"
        f"BACKUP_RETAIN={int(cfg['backup_retain'])}\n"
        f"START_TIMEOUT={int(cfg['start_timeout'])}\n",
        encoding="utf-8",
    )
    root = Path(cfg["instance_dir"])
    for sub in ("", "server", "data", "backups"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    os.system(f"chown -R valheim:valheim {shlex.quote(str(root))}")
    os.environ["HOME"] = "/opt/valheim/steamcmd"
    os.execvp(
        "setpriv",
        ["setpriv", "--reuid=valheim", "--regid=valheim", "--init-groups",
         "python3", "/opt/valheim/app/agent.py", name, "--supervise"],
    )


if __name__ == "__main__":
    main()
