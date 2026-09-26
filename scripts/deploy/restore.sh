#!/usr/bin/env bash
# Restore a dump made by backup.sh. Run as the app user.
#
#   bash scripts/deploy/restore.sh <dump-file | pg-<UTC>.dump>
#       Drill (default): restore into the scratch database cs2coach_restore_check
#       and print row counts. Production is not touched. A bare pg-<UTC>.dump
#       name is fetched from <BACKUP_BUCKET>/postgres/.
#
#   bash scripts/deploy/restore.sh <dump> --into-production --confirm-production-restore
#       Stops api and worker, saves a pre-restore dump, RENAMES the live database
#       to <db>_pre_restore_<UTC> (kept, never dropped), restores into a fresh
#       <db>, prints row counts and starts api and worker again.
set -Eeuo pipefail
# shellcheck source=scripts/deploy/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

SCRATCH_DB="cs2coach_restore_check"
CONTAINER_DUMP="/tmp/cs2coach-restore.dump"
COUNT_TABLES=(accounts demos demo_jobs coaching_events)

usage() { sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

SOURCE=""
INTO_PRODUCTION=0
CONFIRMED=0
while (($#)); do
  case "$1" in
    --into-production) INTO_PRODUCTION=1 ;;
    --confirm-production-restore) CONFIRMED=1 ;;
    -h | --help)
      usage
      exit 0
      ;;
    -*)
      usage >&2
      exit 2
      ;;
    *)
      [[ -z "$SOURCE" ]] || { usage >&2; exit 2; }
      SOURCE="$1"
      ;;
  esac
  shift
done
[[ -n "$SOURCE" ]] || { usage >&2; exit 2; }
if ((INTO_PRODUCTION && !CONFIRMED)); then
  die "--into-production replaces the live database; add --confirm-production-restore to proceed"
fi
((CONFIRMED && !INTO_PRODUCTION)) && die "--confirm-production-restore only applies with --into-production"

require_env_file
POSTGRES_USER="$(env_get POSTGRES_USER cs2coach)"
POSTGRES_DB="$(env_get POSTGRES_DB cs2coach)"
LOCAL_DIR="$(env_get BACKUP_LOCAL_DIR /var/backups/cs2coach)"
[[ "$SCRATCH_DB" != "$POSTGRES_DB" ]] || die "POSTGRES_DB must not be $SCRATCH_DB"

TMP="$(mktemp -d)"
API_STOPPED=0
KEPT_DB=""
cleanup() {
  compose exec -T postgres rm -f "$CONTAINER_DUMP" >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
on_error() {
  local code=$?
  printf 'RESTORE FAILED (exit %s)\n' "$code" >&2
  if [[ -n "$KEPT_DB" ]]; then
    printf 'The previous live database is kept as %s. To put it back:\n' "$KEPT_DB" >&2
    printf '  psql: DROP DATABASE IF EXISTS "%s"; ALTER DATABASE "%s" RENAME TO "%s";\n' \
      "$POSTGRES_DB" "$KEPT_DB" "$POSTGRES_DB" >&2
  fi
  if ((API_STOPPED)); then
    printf 'api and worker are stopped; start them with: docker compose --env-file deploy/.env.production -f docker-compose.yml -f docker-compose.preview.yml -f docker-compose.prod.yml up -d api worker\n' >&2
  fi
}
trap on_error ERR
trap cleanup EXIT

psql_admin() {
  compose exec -T postgres psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d postgres "$@"
}

restore_into() {
  compose exec -T postgres pg_restore --exit-on-error --no-owner --no-privileges \
    -U "$POSTGRES_USER" -d "$1" "$CONTAINER_DUMP"
}

print_counts() {
  local db="$1" table exists rows
  printf '\nRow counts in %s\n' "$db"
  for table in "${COUNT_TABLES[@]}"; do
    exists="$(compose exec -T postgres psql -At -U "$POSTGRES_USER" -d "$db" \
      -c "SELECT to_regclass('public.$table') IS NOT NULL")"
    if [[ "$exists" == "t" ]]; then
      rows="$(compose exec -T postgres psql -At -U "$POSTGRES_USER" -d "$db" -c "SELECT count(*) FROM $table")"
    else
      rows="(table missing)"
    fi
    printf '  %-16s %s\n' "$table" "$rows"
  done
  echo
}

# Resolve the dump: a local file, or a backup name fetched from the bucket.
if [[ -f "$SOURCE" ]]; then
  DUMP="$SOURCE"
else
  NAME="$(basename "$SOURCE")"
  [[ "$NAME" =~ ^pg-[0-9]{8}T[0-9]{6}Z\.dump$ ]] \
    || die "$SOURCE is neither a local file nor a backup name like pg-20260925T033000Z.dump"
  configure_rclone_remotes
  log "Fetching $NAME from cs2backup:$BACKUP_BUCKET/postgres"
  rclone copyto "cs2backup:$BACKUP_BUCKET/postgres/$NAME" "$TMP/$NAME"
  DUMP="$TMP/$NAME"
fi
[[ -s "$DUMP" ]] || die "$DUMP is empty"

log "Checking $DUMP"
compose cp "$DUMP" "postgres:$CONTAINER_DUMP"
compose exec -T postgres pg_restore --list "$CONTAINER_DUMP" >/dev/null \
  || die "$DUMP is not a pg_dump custom-format archive"

if ((!INTO_PRODUCTION)); then
  log "Drill: restoring into scratch database $SCRATCH_DB ($POSTGRES_DB is not touched)"
  psql_admin -c "DROP DATABASE IF EXISTS \"$SCRATCH_DB\" WITH (FORCE)"
  psql_admin -c "CREATE DATABASE \"$SCRATCH_DB\""
  restore_into "$SCRATCH_DB"
  print_counts "$SCRATCH_DB"
  log "Drill complete. Compare the counts with production, then drop the scratch copy:"
  echo "  docker compose --env-file deploy/.env.production -f docker-compose.yml -f docker-compose.preview.yml -f docker-compose.prod.yml exec -T postgres psql -U $POSTGRES_USER -d postgres -c 'DROP DATABASE $SCRATCH_DB'"
  exit 0
fi

TS="$(date -u +%Y%m%d_%H%M%S)"
KEEP_NAME="${POSTGRES_DB}_pre_restore_${TS}"
log "PRODUCTION RESTORE of $POSTGRES_DB from $DUMP"

log "Stopping api and worker"
API_STOPPED=1
compose stop api worker

mkdir -p "$LOCAL_DIR"
SAFETY="$LOCAL_DIR/pre-restore-$TS.dump"
log "Saving the current database to $SAFETY"
(umask 077 && compose exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc >"$SAFETY")
if [[ ! -s "$SAFETY" ]]; then
  # die exits without the ERR trap, so bring the site back before saying so.
  rm -f "$SAFETY"
  log "Pre-restore dump is empty; starting api and worker again"
  compose up -d api worker
  API_STOPPED=0
  die "pre-restore dump was empty; restore aborted, api and worker restarted, the live database was not changed"
fi

log "Renaming $POSTGRES_DB to $KEEP_NAME (kept, not dropped)"
psql_admin -c "ALTER DATABASE \"$POSTGRES_DB\" RENAME TO \"$KEEP_NAME\""
KEPT_DB="$KEEP_NAME"
psql_admin -c "CREATE DATABASE \"$POSTGRES_DB\""
restore_into "$POSTGRES_DB"
print_counts "$POSTGRES_DB"

log "Starting api and worker"
compose up -d api worker
API_STOPPED=0
log "Production restore complete. Run: bash scripts/deploy/prod_smoke.sh https://$(env_get SITE_DOMAIN)"
log "The previous database is kept as $KEEP_NAME and in $SAFETY; drop it once the site is verified."
