#!/usr/bin/env bash
# ACI database backups — dumps `aci` (dev/test) and `aci_bench` (operational)
# from the docker compose `db` service in custom compressed format (-Fc).
#
# Suggested cron entry — the USER installs this manually (the platform NEVER
# edits crontab). Daily at 03:15, keep 14 (= two weeks, default retention):
#   15 3 * * *  cd /home/hien/Data/Projects/ACI && scripts/backup_dbs.sh >> data/backups/cron.log 2>&1
#
# Layout:  data/backups/<YYYYMMDD-HHMMSS>/{aci.dump,aci_bench.dump,manifest.txt}
# Env:     ACI_BACKUP_KEEP   retention count of timestamped dirs (default 14)
#          ACI_BACKUP_ROOT   override backup root (default <repo>/data/backups)
#          ACI_BACKUP_DBS    space-separated DBs to dump (default "aci aci_bench")
#
# Failure behavior: if any dump fails, the partial timestamped dir of that
# attempt is DELETED (pg_restore on a truncated custom-format dump fails
# anyway; the run's log output records the attempt) and retention is SKIPPED
# entirely for that invocation — retention runs only after a fully successful
# backup, so a failed run never evicts good backups.
#
# HONEST LIMIT: these backups live on the SAME disk as the database — a
# host-level failure (disk death, rm -rf mistake, crypto ransom) loses both.
# Copy data/backups/ offsite (rsync to another machine / object storage) at
# least weekly; the manifest sha256s let you verify the offsite copies.
#
# Dependencies: bash, docker compose, and coreutils/util-linux only
# (pg_dump/pg_restore run inside the db container).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_ROOT="${ACI_BACKUP_ROOT:-$REPO_ROOT/data/backups}"
KEEP="${ACI_BACKUP_KEEP:-14}"
# ACI_BACKUP_DBS: space-separated DB list (default "aci aci_bench"). A host
# without the operational DB (Arch after the 2026-10-05 move of aci_bench to
# home-sever) sets ACI_BACKUP_DBS=aci — a missing DB fails the whole run.
read -r -a DBS <<< "${ACI_BACKUP_DBS:-aci aci_bench}"
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="$BACKUP_ROOT/$STAMP"
LOCKFILE="$BACKUP_ROOT/.backup.lock"

cd "$REPO_ROOT" # docker compose resolves docker-compose.yml from cwd
mkdir -p "$BACKUP_ROOT"

# --- concurrency lock: never two dumps at once (flock = util-linux) ---
exec 9>"$LOCKFILE"
if ! flock -n 9; then
  echo "SKIP: another backup run holds the lock ($LOCKFILE); exiting."
  exit 0
fi

# --- idempotency: skip if a backup for this minute already exists ---
if compgen -G "$BACKUP_ROOT/${STAMP%??}*" >/dev/null; then
  echo "SKIP: a backup for minute ${STAMP%??} already exists; exiting."
  exit 0
fi

mkdir -p "$DEST"
: > "$DEST/manifest.txt"

# --- dump each database ---
FAILED=0
for db in "${DBS[@]}"; do
  out="$DEST/$db.dump"
  echo "dumping $db ..."
  if ! docker compose exec -T db pg_dump -U aci -Fc "$db" > "$out.tmp"; then
    echo "ERROR: pg_dump failed for database '$db'" >&2
    rm -f "$out.tmp"
    FAILED=1
    continue
  fi
  mv "$out.tmp" "$out"
  size="$(stat -c%s "$out")"
  sha="$(sha256sum "$out" | awk '{print $1}')"
  printf 'db=%s file=%s size_bytes=%s sha256=%s\n' "$db" "$(basename "$out")" "$size" "$sha" |
    tee -a "$DEST/manifest.txt"
done

# --- failure path: delete the partial dir, skip retention entirely ---
# A partial dump is worse than none: pg_restore on a truncated custom-format
# dump fails anyway, and a failed attempt's dir left on disk would count toward
# the retention budget — KEEP consecutive failed days would otherwise evict
# every good backup. The manifest lines above (tee'd to stdout) record the
# attempt in the log; retention below is unreachable for this invocation.
if (( FAILED )); then
  echo
  echo "=== backup summary ==="
  echo "destination : $DEST"
  cat "$DEST/manifest.txt"
  rm -rf "$DEST"
  echo "removed     : $DEST (partial dump of a failed attempt)"
  echo "retention   : skipped — a failed run never prunes good backups"
  echo "STATUS      : FAILED — one or more dumps errored, see above"
  exit 1
fi

# --- prune: keep only the newest $KEEP timestamped dirs ---
# (reached only after a fully successful backup — see the failure path above)
mapfile -t dirs < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '20??????-??????' | sort)
pruned=0
if (( ${#dirs[@]} > KEEP )); then
  for (( i = 0; i < ${#dirs[@]} - KEEP; i++ )); do
    echo "pruning old backup: ${dirs[$i]}"
    rm -rf "${dirs[$i]}"
    pruned=$((pruned + 1))
  done
fi

# --- summary ---
echo
echo "=== backup summary ==="
echo "destination : $DEST"
cat "$DEST/manifest.txt"
echo "kept        : $(( ${#dirs[@]} - pruned )) timestamped backup dir(s) (retention ACI_BACKUP_KEEP=$KEEP)"
echo "pruned      : $pruned"
echo "STATUS      : OK"
