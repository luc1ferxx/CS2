#!/usr/bin/env bash
# Nightly backup, run as the app user (systemd: cs2coach-backup.timer):
#   1. pg_dump -Fc of the production database to BACKUP_LOCAL_DIR/pg-<UTC>.dump,
#      checked non-empty and parseable by pg_restore --list;
#   2. upload to <BACKUP_BUCKET>/postgres/ and apply retention (newest
#      BACKUP_LOCAL_KEEP local dumps, remote dumps younger than BACKUP_REMOTE_KEEP_DAYS);
#   3. mirror the artifact bucket into <BACKUP_BUCKET>/artifacts/. Objects the
#      mirror would delete or overwrite move to artifacts-replaced/<UTC>/ and are
#      purged after BACKUP_REMOTE_KEEP_DAYS. Bucket versioning or lifecycle rules
#      on the provider are the stronger protection; this is the floor.
# Any failure exits non-zero with a "BACKUP FAILED" line.
set -Eeuo pipefail
# shellcheck source=scripts/deploy/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

STEP="startup"
PARTIAL=""
trap 'printf "BACKUP FAILED during %s (exit %s)\n" "$STEP" "$?" >&2' ERR
trap 'if [[ -n "$PARTIAL" ]]; then rm -f "$PARTIAL" "$PARTIAL.list"; fi' EXIT
fail() { printf 'BACKUP FAILED during %s: %s\n' "$STEP" "$*" >&2; exit 1; }

require_env_file
POSTGRES_USER="$(env_get POSTGRES_USER cs2coach)"
POSTGRES_DB="$(env_get POSTGRES_DB cs2coach)"
LOCAL_DIR="$(env_get BACKUP_LOCAL_DIR /var/backups/cs2coach)"
LOCAL_KEEP="$(env_get BACKUP_LOCAL_KEEP 14)"
REMOTE_KEEP_DAYS="$(env_get BACKUP_REMOTE_KEEP_DAYS 30)"
SYNC_ARTIFACTS="$(env_get BACKUP_SYNC_ARTIFACTS 1)"
[[ "$LOCAL_KEEP" =~ ^[1-9][0-9]*$ ]] || fail "BACKUP_LOCAL_KEEP must be a positive integer"
[[ "$REMOTE_KEEP_DAYS" =~ ^[1-9][0-9]*$ ]] || fail "BACKUP_REMOTE_KEEP_DAYS must be a positive integer"
configure_rclone_remotes

umask 077
mkdir -p "$LOCAL_DIR"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="pg-$TS.dump"
DUMP="$LOCAL_DIR/$NAME"
PARTIAL="$DUMP.partial"
REMOTE_DUMPS="cs2backup:$BACKUP_BUCKET/postgres"

STEP="pg_dump"
log "Dumping database $POSTGRES_DB"
compose exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc >"$PARTIAL"

STEP="dump verification"
[[ -s "$PARTIAL" ]] || fail "the dump is empty"
compose exec -T postgres pg_restore --list <"$PARTIAL" >"$PARTIAL.list"
tables="$(grep -c 'TABLE DATA' "$PARTIAL.list" || true)"
((tables > 0)) || fail "pg_restore --list found no table data in the dump"
mv "$PARTIAL" "$DUMP"
rm -f "$PARTIAL.list"
PARTIAL=""
log "Dump OK: $DUMP ($(du -h "$DUMP" | cut -f1), $tables tables with data)"

STEP="dump upload"
rclone copyto "$DUMP" "$REMOTE_DUMPS/$NAME"
rclone lsf "$REMOTE_DUMPS/$NAME" | grep -qx "$NAME" || fail "uploaded dump not found at $REMOTE_DUMPS/$NAME"
log "Uploaded to $REMOTE_DUMPS/$NAME"

STEP="local retention"
find "$LOCAL_DIR" -maxdepth 1 -type f -name 'pg-*.dump' -printf '%f\n' | sort -r \
  | tail -n +"$((LOCAL_KEEP + 1))" | while IFS= read -r old; do
    rm -f -- "$LOCAL_DIR/$old"
    log "Removed local $old"
  done

STEP="remote dump retention"
rclone delete --min-age "${REMOTE_KEEP_DAYS}d" --include 'pg-*.dump' "$REMOTE_DUMPS"

if [[ "$SYNC_ARTIFACTS" == "1" ]]; then
  STEP="artifact mirror"
  [[ -n "$ARTIFACT_BUCKET" ]] || fail "OBJECT_STORAGE_BUCKET is not set"
  log "Mirroring cs2artifacts:$ARTIFACT_BUCKET into $BACKUP_BUCKET/artifacts"
  rclone sync --fast-list --checksum \
    --backup-dir "cs2backup:$BACKUP_BUCKET/artifacts-replaced/$TS" \
    "cs2artifacts:$ARTIFACT_BUCKET" "cs2backup:$BACKUP_BUCKET/artifacts"

  STEP="artifact retention"
  cutoff="$(date -u -d "$REMOTE_KEEP_DAYS days ago" +%Y%m%dT%H%M%SZ)"
  # Directory names, not object mtimes: moved objects keep their original mtime.
  # The prefix does not exist until the mirror first replaces something.
  { rclone lsf --dirs-only "cs2backup:$BACKUP_BUCKET/artifacts-replaced" 2>/dev/null || true; } \
    | while IFS= read -r dir; do
      dir="${dir%/}"
      if [[ "$dir" =~ ^[0-9]{8}T[0-9]{6}Z$ && "$dir" < "$cutoff" ]]; then
        rclone purge "cs2backup:$BACKUP_BUCKET/artifacts-replaced/$dir"
        log "Purged artifacts-replaced/$dir"
      fi
    done
else
  log "BACKUP_SYNC_ARTIFACTS is not 1; skipping the artifact mirror"
fi

STEP="done"
log "Backup complete: $NAME"
