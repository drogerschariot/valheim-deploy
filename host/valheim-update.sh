#!/usr/bin/env bash
# Updates one instance to the current public Valheim build.
# Usage: valheim-update.sh <instance> [--check]
#
# There is no version pin to honour: a client can only join a server on the
# same build and Steam updates clients automatically, so a pinned server would
# lock every player out on patch day. The safety lives in the procedure instead
# -- back up first, health-check after, and say plainly when it did not come
# back.

set -euo pipefail

[[ "${1:-}" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo "bad instance name" >&2; exit 2; }
INSTANCE="$1"
# shellcheck disable=SC1090
source "/etc/valheim/${INSTANCE}.conf"

SERVER_DIR="${INSTANCE_DIR}/server"
STEAM_HOME="/opt/valheim/steamcmd"
STEAMCMD="${STEAM_HOME}/steamcmd.sh"
APP_ID=896660
SERVICE="valheim-server-${INSTANCE}"
BACKUP_SCRIPT="/opt/valheim/bin/valheim-backup.sh"
RUN_AS=valheim
MANIFEST="${SERVER_DIR}/steamapps/appmanifest_${APP_ID}.acf"

check_only=0
[[ "${2:-}" == "--check" ]] && check_only=1

# steamcmd writes under $HOME, and this may run with root's or a sandboxed
# caller's HOME. Pin it to a directory the service account owns.
as_steam() {
  if [[ "$(id -un)" == "$RUN_AS" ]]; then
    env HOME="$STEAM_HOME" "$@"
  else
    runuser -u "$RUN_AS" -- env HOME="$STEAM_HOME" "$@"
  fi
}

installed_build() {
  if [[ -f "$MANIFEST" ]]; then
    awk -F'"' '/"buildid"/ {print $4; exit}' "$MANIFEST"
  else
    echo "unknown"
  fi
}

available_build() {
  as_steam "$STEAMCMD" \
    +login anonymous \
    +app_info_update 1 \
    +app_info_print "$APP_ID" \
    +quit 2>/dev/null \
    | awk '/"public"/ {inpublic=1} inpublic && /"buildid"/ {print $2; exit}' \
    | tr -d '"'
}

current=$(installed_build)
available=$(available_build || true)
[[ -n "$available" ]] || available="unknown"

echo "Installed build: ${current}"
echo "Available build: ${available}"

if [[ "$check_only" == "1" ]]; then
  if [[ "$current" == "$available" && "$current" != "unknown" ]]; then
    echo "Server is current."
  else
    echo "An update is available (or the build could not be determined)."
  fi
  exit 0
fi

if [[ "$current" == "$available" && "$current" != "unknown" ]]; then
  echo "Already on build ${current}; nothing to do."
  exit 0
fi

# Taken before anything stops, and a failure aborts the update. An update that
# cannot be rolled back onto a known-good world is not worth starting. A server
# that has never saved has nothing to protect, which is not a failure.
echo "Taking a pre-update backup..."
if [[ -d "${INSTANCE_DIR}/data/worlds_local" ]] && \
   compgen -G "${INSTANCE_DIR}/data/worlds_local/*" > /dev/null; then
  runuser -u "$RUN_AS" -- "$BACKUP_SCRIPT" "$INSTANCE"
else
  echo "No world on disk yet; nothing to back up."
fi

was_active=0
if systemctl is-active --quiet "$SERVICE"; then
  was_active=1
  echo "Stopping ${SERVICE} (SIGINT, up to 120s for the world write)..."
  systemctl stop "$SERVICE"
fi

echo "Updating from Steam..."
if ! as_steam "$STEAMCMD" \
  +force_install_dir "$SERVER_DIR" \
  +login anonymous \
  +app_update "$APP_ID" validate \
  +quit; then
  echo "SteamCMD failed. The previous install is still in place." >&2
  if [[ "$was_active" == "1" ]]; then
    echo "Restarting the previous server build..." >&2
    systemctl start "$SERVICE"
  fi
  exit 1
fi

echo "Now on build $(installed_build)."

if [[ "$was_active" == "0" ]]; then
  echo "Server was not running before the update; leaving it stopped."
  exit 0
fi

echo "Starting ${SERVICE}..."
systemctl start "$SERVICE"

# Readiness comes from the log, not the socket: the UDP port binds well before
# the world finishes loading. Bounded to this run by invocation ID, because the
# journal still holds the readiness line from the build just replaced.
invocation=$(systemctl show -p InvocationID --value "$SERVICE")
deadline=$((SECONDS + START_TIMEOUT))
while (( SECONDS < deadline )); do
  # Matched against a variable, not through a pipe. Under pipefail,
  # `journalctl | grep -q` returns 141 once the log outgrows the pipe buffer:
  # grep exits on the first match and journalctl takes SIGPIPE, so the check
  # would fail precisely because it found what it was looking for.
  log=$(journalctl "_SYSTEMD_INVOCATION_ID=${invocation}" --no-pager)
  if grep -qE 'Game server connected|DungeonDB Start' <<<"$log"; then
    echo "Server is up and the world is loaded."
    exit 0
  fi
  if ! systemctl is-active --quiet "$SERVICE"; then
    echo "${SERVICE} exited during startup. Recent log:" >&2
    tail -40 <<<"$log" >&2
    exit 1
  fi
  sleep 5
done

echo "Server did not report a loaded world within ${START_TIMEOUT}s." >&2
echo "It may still be loading a large world. Check: journalctl -u ${SERVICE} -f" >&2
exit 1
