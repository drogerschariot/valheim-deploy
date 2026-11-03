#!/usr/bin/env bash
# Copies a Valheim world out of the server's directory tree into the instance's
# backups directory and prunes old archives. Usage: valheim-backup.sh <instance>
#
# This exists alongside Valheim's own rotating backups, not instead of them.
# Those rotate inside worlds_local, on the same disk and in the same tree as
# the world they protect, so they survive a bad save but not a lost directory.

set -euo pipefail

[[ "${1:-}" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo "bad instance name" >&2; exit 2; }
# shellcheck disable=SC1090
source "/etc/valheim/${1}.conf"

DATA_DIR="${INSTANCE_DIR}/data"
WORLD_DIR="${DATA_DIR}/worlds_local"
BACKUP_DIR="${INSTANCE_DIR}/backups"
WORLD="${WORLD_NAME}"
RETAIN="${BACKUP_RETAIN}"

timestamp=$(date +%Y%m%d-%H%M%S)
archive="${BACKUP_DIR}/${WORLD}-${timestamp}.tar.gz"

if [[ ! -d "$WORLD_DIR" ]]; then
  echo "No world directory at ${WORLD_DIR}; nothing to back up." >&2
  exit 0
fi

# A world save is a directory, not a .db/.fwl pair: per-zone *.chunk files plus
# a generation-numbered _main.<N>.db2/.fwl2/.chunks, with a _main.<N>.ok marker
# written only once that generation finishes. Valheim's rotating auto-backups
# are full copies of that directory, named ${WORLD}_backup_auto-<timestamp>.
#
# Prefer the newest of those over the live directory: the server writes them,
# so they are consistent by construction, whereas copying the live directory
# during a save can catch a generation half-written.
newest_backup=$(find "$WORLD_DIR" -maxdepth 1 -type d \
  -name "${WORLD}_backup_auto-*" 2>/dev/null | sort -r | head -1 || true)

source_note="server backup"
if [[ -n "$newest_backup" ]]; then
  source_dir="$newest_backup"
else
  source_dir="${WORLD_DIR}/${WORLD}"
  source_note="live world (may be mid-save)"
fi

# Older worlds, and worlds not yet migrated, are a flat .db/.fwl pair. Accept
# those too rather than refusing to back them up.
flat=0
if [[ ! -d "$source_dir" ]] && [[ -f "${WORLD_DIR}/${WORLD}.db" ]]; then
  flat=1
  source_note="live world, flat format (may be mid-save)"
elif [[ ! -d "$source_dir" ]] || ! compgen -G "${source_dir}/_main.*.ok" > /dev/null; then
  # The .ok marker's absence covers a missing world, an empty directory, and a
  # save still in progress alike.
  echo "World files for '${WORLD}' not found under ${WORLD_DIR}." >&2
  echo "A new world is first written at its first save, so if the server has" >&2
  echo "only just started there may be nothing to back up yet." >&2
  exit 1
fi

install -d -m 0750 "$BACKUP_DIR"

# Staged into one directory and archived in a single pass, because tar cannot
# append to a compressed archive.
staging=$(mktemp -d)
trap 'rm -rf -- "$staging"' EXIT

if [[ "$flat" == "1" ]]; then
  cp -- "${WORLD_DIR}/${WORLD}".{db,fwl} "$staging/"
else
  cp -r -- "$source_dir" "${staging}/${WORLD}"
fi

# The admin list is the other half of a restore: a world without it comes back
# with nobody able to kick or ban.
if [[ -f "${DATA_DIR}/adminlist.txt" ]]; then
  cp -- "${DATA_DIR}/adminlist.txt" "${staging}/adminlist.txt"
fi

tar -czf "$archive" -C "$staging" .

size=$(du -h "$archive" | cut -f1)
echo "Backed up ${WORLD} from ${source_note}: ${archive} (${size})"

# Prune by count rather than age: a server nobody has played on for a month
# should still keep its last known-good worlds.
mapfile -t stale < <(
  find "$BACKUP_DIR" -maxdepth 1 -name "${WORLD}-*.tar.gz" -printf '%T@ %p\n' \
    | sort -rn \
    | tail -n "+$((RETAIN + 1))" \
    | cut -d' ' -f2-
)

for old in "${stale[@]:-}"; do
  [[ -n "$old" ]] || continue
  echo "Pruning ${old}"
  rm -f -- "$old"
done
