#!/usr/bin/env bash
# Build and (re)start the production stack on the VPS, wait for health through
# Caddy, then run the anonymous production smoke.
#
#   bash scripts/deploy/deploy.sh                  # fast-forward DEPLOY_BRANCH and deploy it
#   bash scripts/deploy/deploy.sh --rollback <sha> # check out <sha> (detached) and deploy it
#   add --skip-backup to either to skip the pre-migration dump (last resort)
#
# Run as the app user (member of the docker group), not root. Every check that
# can refuse runs before a running service changes:
#   - the target commit must know every schema migration the database has
#     applied; otherwise its api and worker refuse to start (crash loop), so the
#     script stops and names the pre-deploy dump to restore first;
#   - a target that adds migrations gets a database dump (backup.sh --db-only)
#     before `up`, recorded in deploy/.deploy-history;
#   - a rollback whose per-commit images (cs2coach-backend:<sha> for api and
#     worker, cs2coach-frontend:<sha>) still exist starts them without a build.
# After the start: /health, then /health/worker from a worker that has run past
# the heartbeat window, then prod_smoke.sh. A passing deploy tags the images
# its containers run as <sha> and latest, keeps the images of the last 3
# deployed commits and prunes dangling images and build cache beyond 5 GB.
set -Eeuo pipefail
# shellcheck source=scripts/deploy/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

usage() {
  awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"
}

HISTORY="$REPO_ROOT/deploy/.deploy-history"
KEEP_DEPLOYS=3
BUILD_CACHE_KEEP=5GB
BACKEND_IMAGE_REPO=cs2coach-backend
FRONTEND_IMAGE_REPO=cs2coach-frontend
# docker-compose.prod.yml either names these images by commit
# (cs2coach-backend:${DEPLOY_SHA}, exported below) or leaves compose's default
# <project>-<service> names; the rollback reuse path handles both (image_naming).
# Tagging after a deploy reads the containers' image ids and needs no names.
RUN_IMAGE_API=cs2coach-api
RUN_IMAGE_WORKER=cs2coach-worker
RUN_IMAGE_FRONTEND=cs2coach-frontend
# A recreated worker inherits the previous worker's heartbeat for up to 90 s
# (WORKER_HEARTBEAT_ALIVE_SECONDS, backend/app/services/diagnostics.py), so a
# /health/worker 200 counts only from a worker container older than that.
WORKER_SETTLE_SECONDS=95
WORKER_WAIT_SECONDS=60

ROLLBACK_SHA=""
SKIP_BACKUP=0
while (($#)); do
  case "$1" in
    --rollback)
      [[ $# -ge 2 && -n "$2" ]] || { usage >&2; exit 2; }
      ROLLBACK_SHA="$2"
      shift 2
      ;;
    --skip-backup)
      SKIP_BACKUP=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

[[ "$(id -u)" -ne 0 ]] || die "run as the app user (e.g. cs2coach), not root"
require_env_file
# Assignments only: the template's own comments mention both placeholders.
PLACEHOLDER_KEYS="$(grep -vE '^[[:space:]]*(#|$)' "$ENV_FILE" \
  | grep -E 'CHANGE_ME|coach\.example\.com' \
  | sed -E 's/^[[:space:]]*(export[[:space:]]+)?([^=]*)=.*/\2/' \
  | paste -sd ' ' - || true)"
if [[ -n "$PLACEHOLDER_KEYS" ]]; then
  die "$ENV_FILE still has CHANGE_ME or coach.example.com placeholders in: $PLACEHOLDER_KEYS"
fi
if [[ "$(stat -c '%a' "$ENV_FILE" 2>/dev/null || echo 600)" != "600" ]]; then
  log "WARNING: $ENV_FILE holds secrets; run: chmod 600 $ENV_FILE"
fi

# /privacy promises a contact for privacy questions and removal requests.
[[ -n "$(env_get NEXT_PUBLIC_PRIVACY_CONTACT)" ]]   || die "NEXT_PUBLIC_PRIVACY_CONTACT must be set: /privacy shows it for privacy questions and removal requests"

SITE_DOMAIN="$(env_get SITE_DOMAIN)"
[[ "$SITE_DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || die "SITE_DOMAIN must be a bare host name such as coach.example.com"
BASE_URL="https://$SITE_DOMAIN"
BRANCH="$(env_get DEPLOY_BRANCH main)"
HEALTH_TIMEOUT="$(env_get DEPLOY_HEALTH_TIMEOUT_SECONDS 300)"
[[ "$HEALTH_TIMEOUT" =~ ^[0-9]+$ ]] || die "DEPLOY_HEALTH_TIMEOUT_SECONDS must be a whole number"
POSTGRES_USER="$(env_get POSTGRES_USER cs2coach)"
POSTGRES_DB="$(env_get POSTGRES_DB cs2coach)"

cd "$REPO_ROOT"
if ! git diff --quiet || ! git diff --cached --quiet; then
  die "the checkout has local changes to tracked files; deploy only committed code"
fi
PREVIOUS="$(git rev-parse --short HEAD)"

# --- 1. Resolve the target without touching the checkout ----------------------
if [[ -n "$ROLLBACK_SHA" ]]; then
  MODE="rollback"
  if ! git cat-file -e "${ROLLBACK_SHA}^{commit}" 2>/dev/null; then
    git fetch --quiet origin
  fi
  TARGET="$(git rev-parse --verify --quiet "${ROLLBACK_SHA}^{commit}")" || die "unknown commit: $ROLLBACK_SHA"
  log "Rolling back from $PREVIOUS to $(git rev-parse --short "$TARGET")"
else
  MODE="deploy"
  log "Fetching $BRANCH (currently at $PREVIOUS)"
  git fetch --quiet origin "$BRANCH"
  TARGET="$(git rev-parse --verify --quiet 'FETCH_HEAD^{commit}')" || die "could not resolve origin/$BRANCH"
  if git show-ref --verify --quiet "refs/heads/$BRANCH" \
    && ! git merge-base --is-ancestor "refs/heads/$BRANCH" "$TARGET"; then
    die "local $BRANCH has commits that origin/$BRANCH does not; deploy only pushed code"
  fi
fi
TARGET_SHORT="$(git rev-parse --short "$TARGET")"

# --- 2. Schema pre-check (services untouched) --------------------------------
# Versions the target's migration runner knows (SchemaMigration(version="...")).
RUNNER="backend/app/migrations/runner.py"
TARGET_VERSIONS="$(git show "$TARGET:$RUNNER" 2>/dev/null \
  | sed -nE 's/^[[:space:]]*version="([0-9]+)",?[[:space:]]*$/\1/p' | LC_ALL=C sort -u || true)"
if [[ -z "$TARGET_VERSIONS" ]] && git cat-file -e "$TARGET:$RUNNER" 2>/dev/null; then
  die "found no schema versions in $TARGET_SHORT:$RUNNER; the version pattern in deploy.sh no longer matches it"
fi

psql_prod() { # SQL: one query against the live database, unaligned output
  compose exec -T postgres psql -v ON_ERROR_STOP=1 -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"
}

SCHEMA_KNOWN=0
DB_VERSIONS=""
if [[ -n "$(compose ps --status running -q postgres 2>/dev/null || true)" ]]; then
  table="$(psql_prod "SELECT to_regclass('public.app_schema_migrations') IS NOT NULL")" \
    || die "could not read the schema state from postgres; running services were not changed"
  if [[ "$table" == "t" ]]; then
    DB_VERSIONS="$(psql_prod "SELECT version FROM app_schema_migrations ORDER BY version")" \
      || die "could not read the applied schema migrations; running services were not changed"
    SCHEMA_KNOWN=1
  else
    log "The database has no schema migrations yet; nothing to check or back up"
  fi
elif [[ -s "$HISTORY" ]]; then
  die "postgres is not running, so the schema of $TARGET_SHORT cannot be checked against the database. Start it (dc up -d postgres, see docs/vps_deploy_v1.md) and rerun; running services were not changed"
else
  log "No database yet (first deploy); skipping the schema check"
fi

UNKNOWN_MIGRATIONS=""
NEW_MIGRATIONS=""
if ((SCHEMA_KNOWN)); then
  db_sorted="$(printf '%s\n' "$DB_VERSIONS" | sed '/^$/d' | LC_ALL=C sort -u)"
  target_sorted="$(printf '%s\n' "$TARGET_VERSIONS" | sed '/^$/d' | LC_ALL=C sort -u)"
  UNKNOWN_MIGRATIONS="$(LC_ALL=C comm -23 <(printf '%s\n' "$db_sorted") <(printf '%s\n' "$target_sorted") | sed '/^$/d' | paste -sd ' ' -)"
  NEW_MIGRATIONS="$(LC_ALL=C comm -13 <(printf '%s\n' "$db_sorted") <(printf '%s\n' "$target_sorted") | sed '/^$/d' | paste -sd ' ' -)"
fi

# The dump recorded before VERSION was applied (backup= on a history line whose
# migrations= lists it), or nothing.
pre_migration_dump() { # VERSION
  [[ -f "$HISTORY" ]] || return 0
  { grep -E "(^| )migrations=([0-9]+,)*$1(,|[[:space:]]|$)" "$HISTORY" || true; } \
    | grep -oE 'backup=pg-[0-9]{8}T[0-9]{6}Z\.dump' | sed 's/^backup=//' | head -n 1 || true
}

if [[ -n "$UNKNOWN_MIGRATIONS" ]]; then
  first_unknown="${UNKNOWN_MIGRATIONS%% *}"
  rerun="bash scripts/deploy/deploy.sh"
  if [[ "$MODE" == "rollback" ]]; then rerun+=" --rollback $TARGET_SHORT"; fi
  dump="$(pre_migration_dump "$first_unknown")"
  if [[ -n "$dump" ]]; then
    dump_note="deploy/.deploy-history records $dump as the dump taken before $first_unknown."
  else
    dump="<pg-...dump>"
    dump_note="deploy/.deploy-history records no dump before $first_unknown: use the newest nightly pg-*.dump older than the deploy that applied it."
  fi
  die "$(
    cat <<EOF
the database has schema migrations that $TARGET_SHORT does not know: $UNKNOWN_MIGRATIONS
Its api and worker would refuse to start and restart in a loop. Nothing was changed.
Fix forward, or restore the dump taken before migration $first_unknown (this loses
every change made since that dump; see docs/vps_deploy_v1.md section 9) and rerun:
  bash scripts/deploy/restore.sh $dump --into-production --confirm-production-restore --no-start
  $rerun
$dump_note
EOF
  )"
fi
if [[ -n "$NEW_MIGRATIONS" ]]; then
  log "$TARGET_SHORT applies schema migrations $NEW_MIGRATIONS at startup; the database is dumped before services change"
fi

# --- 3. Check out the target (running services keep running) ----------------
if [[ "$MODE" == "rollback" ]]; then
  git checkout --quiet --detach "$TARGET"
else
  log "Updating $BRANCH to $TARGET_SHORT"
  git checkout --quiet "$BRANCH"
  git merge --ff-only --quiet "$TARGET"
fi
CURRENT="$(git rev-parse --short HEAD)"
SHA_TAG="$(git rev-parse --short=12 HEAD)"
BACKEND_TAG="$BACKEND_IMAGE_REPO:$SHA_TAG"
FRONTEND_TAG="$FRONTEND_IMAGE_REPO:$SHA_TAG"
# For compose files that name the api/worker/frontend images by commit.
export DEPLOY_SHA="$SHA_TAG"
BACKUP_NAME=""

# What to do after a failure once services may have changed. A first deploy
# has nothing older to roll back to.
next_step() {
  if [[ "$PREVIOUS" == "$CURRENT" ]]; then
    printf 'fix the cause (often a value in %s) and rerun: bash scripts/deploy/deploy.sh' "$ENV_FILE"
  elif [[ -n "$NEW_MIGRATIONS" ]]; then
    local dump="$BACKUP_NAME"
    [[ -n "$dump" && "$dump" != "skipped" ]] || dump="<the newest pg-*.dump from before this deploy>"
    printf 'fix forward. %s applies schema migrations %s at startup, and once they are in the database a plain rollback to %s is refused (deploy.sh --rollback checks and says so). To go back, restore the pre-deploy dump first, then roll back:\n  bash scripts/deploy/restore.sh %s --into-production --confirm-production-restore --no-start\n  bash scripts/deploy/deploy.sh --rollback %s' \
      "$CURRENT" "$NEW_MIGRATIONS" "$PREVIOUS" "$dump" "$PREVIOUS"
  else
    printf 'fix forward, or roll back with: bash scripts/deploy/deploy.sh --rollback %s' "$PREVIOUS"
  fi
}

image_exists() { docker image inspect "$1" >/dev/null 2>&1; }

# How the target's compose files name the api, worker and frontend images:
#   commit   cs2coach-backend:<sha> and cs2coach-frontend:<sha>: run them as is
#   default  cs2coach-api, cs2coach-worker, cs2coach-frontend: retag onto them
#   other    anything else: build
image_naming() {
  local images
  images="$(compose config --images 2>/dev/null)" || { echo other; return 0; }
  if grep -qxF "$BACKEND_TAG" <<<"$images" && grep -qxF "$FRONTEND_TAG" <<<"$images"; then
    echo commit
  elif grep -qxF "$RUN_IMAGE_API" <<<"$images" \
    && grep -qxF "$RUN_IMAGE_WORKER" <<<"$images" \
    && grep -qxF "$RUN_IMAGE_FRONTEND" <<<"$images"; then
    echo default
  else
    echo other
  fi
}

log "Validating deploy/Caddyfile"
compose run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile \
  || die "deploy/Caddyfile is invalid; running services were not changed"

# --- 4. Images: reuse the commit's tagged images on a rollback, else build ---
REUSE_IMAGES=0
RETAG_IMAGES=0
if [[ "$MODE" == "rollback" ]] && image_exists "$BACKEND_TAG" && image_exists "$FRONTEND_TAG"; then
  case "$(image_naming)" in
    commit) REUSE_IMAGES=1 ;;
    default)
      REUSE_IMAGES=1
      RETAG_IMAGES=1
      ;;
    *) log "WARNING: unexpected image names in the compose files of $CURRENT; building instead of reusing $BACKEND_TAG" ;;
  esac
  if ((REUSE_IMAGES)); then log "Reusing $BACKEND_TAG and $FRONTEND_TAG; no build"; fi
fi
if ((!REUSE_IMAGES)); then
  log "Building images for $CURRENT"
  compose build --pull || die "image build failed; running services were not changed (checkout is at $CURRENT, was $PREVIOUS)"
fi

# --- 5. Dump the database before migrations run -------------------------------
if [[ -n "$NEW_MIGRATIONS" ]]; then
  if ((SKIP_BACKUP)); then
    BACKUP_NAME="skipped"
    log "WARNING: --skip-backup: deploying migrations $NEW_MIGRATIONS without a pre-deploy dump"
  else
    backup_log="$(mktemp)"
    if ! bash "$REPO_ROOT/scripts/deploy/backup.sh" --db-only | tee "$backup_log"; then
      rm -f "$backup_log"
      die "pre-deploy backup failed; running services were not changed (checkout is at $CURRENT, was $PREVIOUS). Fix the backup and rerun, or add --skip-backup to deploy without one"
    fi
    BACKUP_NAME="$(sed -nE 's/.*Backup complete: (pg-[0-9]{8}T[0-9]{6}Z\.dump)$/\1/p' "$backup_log" | tail -n 1)"
    rm -f "$backup_log"
    [[ -n "$BACKUP_NAME" ]] || die "backup.sh succeeded but reported no dump name; running services were not changed"
  fi
  # Recorded now: a deploy that fails after `up` writes no success line, and
  # the rollback hint needs this dump.
  printf '%s backup %s previous=%s backup=%s migrations=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$CURRENT" "$PREVIOUS" "$BACKUP_NAME" "${NEW_MIGRATIONS// /,}" >>"$HISTORY"
fi

# --- 6. Start ----------------------------------------------------------------
maintenance_begin "deploy $CURRENT"
trap maintenance_end EXIT

up_args=(-d --remove-orphans)
if ((RETAG_IMAGES)); then
  # api and worker run one backend image after a reuse; a build makes two
  # equivalent ones from the same context.
  if ! { docker tag "$BACKEND_TAG" "$RUN_IMAGE_API" \
    && docker tag "$BACKEND_TAG" "$RUN_IMAGE_WORKER" \
    && docker tag "$FRONTEND_TAG" "$RUN_IMAGE_FRONTEND"; }; then
    die "could not retag $BACKEND_TAG / $FRONTEND_TAG onto the run names; running containers were not changed"
  fi
fi
if ((REUSE_IMAGES)); then up_args+=(--no-build); fi
log "Starting services"
# caddy and frontend wait for the api healthcheck, so an api that crashes on
# startup (e.g. config validation) fails here rather than in the health wait.
if ! compose up "${up_args[@]}"; then
  compose ps || true
  compose logs --tail 60 api worker frontend caddy || true
  die "services failed to start; $(next_step)"
fi

# Caddy reads its config only at startup, and git replaces deploy/Caddyfile
# with a new inode that a running container's single-file bind mount never
# sees. Restart caddy (which re-binds the mount; certificates persist in
# caddy_data) whenever what it has mounted differs from the checkout. This
# covers a pulled change, a rollback across one, and an earlier failed deploy.
if ! compose exec -T caddy cat /etc/caddy/Caddyfile 2>/dev/null | cmp -s - "$REPO_ROOT/deploy/Caddyfile"; then
  log "deploy/Caddyfile changed; restarting caddy to load it"
  compose restart caddy || die "caddy failed to restart; $(next_step)"
fi

log "Waiting up to ${HEALTH_TIMEOUT}s for $BASE_URL/health"
deadline=$((SECONDS + HEALTH_TIMEOUT))
while :; do
  status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "$BASE_URL/health" 2>/dev/null || true)"
  [[ "$status" == "200" ]] && break
  if ((SECONDS >= deadline)); then
    compose ps || true
    compose logs --tail 40 caddy api || true
    die "$BASE_URL/health did not return 200 within ${HEALTH_TIMEOUT}s (last status: ${status:-none}); $(next_step)"
  fi
  sleep 5
done
log "Health OK"

# "<state> <uptime seconds> <restart count>" of the worker container.
worker_container_state() {
  local id state started restarts now started_s
  id="$(compose ps -a -q worker 2>/dev/null | head -n 1 || true)"
  if [[ -z "$id" ]] \
    || ! read -r state started restarts < <(docker inspect -f '{{.State.Status}} {{.State.StartedAt}} {{.RestartCount}}' "$id" 2>/dev/null); then
    echo "missing 0 0"
    return 0
  fi
  now="$(date +%s)"
  started_s="$(date -d "$started" +%s 2>/dev/null)" || started_s="$now"
  echo "$state $((now - started_s)) ${restarts:-0}"
}

log "Waiting for $BASE_URL/health/worker from a worker running at least ${WORKER_SETTLE_SECONDS}s"
deadline=$((SECONDS + WORKER_SETTLE_SECONDS + WORKER_WAIT_SECONDS))
# Restarts from before this wait (an unchanged worker keeps its count) do not
# count; a crash loop that started earlier never reaches the settle time.
read -r _ _ restarts_before < <(worker_container_state)
while :; do
  read -r w_state w_uptime w_restarts < <(worker_container_state)
  status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "$BASE_URL/health/worker" 2>/dev/null || true)"
  if [[ "$w_state" == "running" && "$status" == "200" ]] && ((w_uptime >= WORKER_SETTLE_SECONDS)); then
    break
  fi
  if ((w_restarts - restarts_before >= 2)); then
    compose ps worker || true
    compose logs --tail 60 worker || true
    die "the worker container restarted $((w_restarts - restarts_before)) times while waiting; $(next_step)"
  fi
  if ((SECONDS >= deadline)); then
    compose ps worker || true
    compose logs --tail 60 worker || true
    die "$BASE_URL/health/worker did not return 200 from a worker running ${WORKER_SETTLE_SECONDS}s (last: HTTP ${status:-none}, worker $w_state for ${w_uptime}s); $(next_step)"
  fi
  sleep 5
done
log "Worker OK"

if ! bash "$REPO_ROOT/scripts/deploy/prod_smoke.sh" "$BASE_URL"; then
  die "production smoke failed; inspect with 'docker compose ... logs', then $(next_step)"
fi

entry="$(date -u +%Y-%m-%dT%H:%M:%SZ) $MODE $CURRENT previous=$PREVIOUS"
if [[ -n "$BACKUP_NAME" ]]; then entry+=" backup=$BACKUP_NAME"; fi
if [[ -n "$NEW_MIGRATIONS" ]]; then entry+=" migrations=${NEW_MIGRATIONS// /,}"; fi
printf '%s\n' "$entry" >>"$HISTORY"

# --- 7. Tag what passed, keep the last KEEP_DEPLOYS commits, prune ------------
# Tag the image ids the containers run (the api's stands for api and worker)
# as <sha> and latest: latest is what compose files that name images by
# DEPLOY_SHA start when it is unset (restore.sh sets it, a manual `dc up` not).
tag_running_image() { # SERVICE REPO
  local id image
  id="$(compose ps -q "$1" 2>/dev/null | head -n 1 || true)"
  [[ -n "$id" ]] || return 1
  image="$(docker inspect -f '{{.Image}}' "$id" 2>/dev/null)" || return 1
  docker tag "$image" "$2:$SHA_TAG" && docker tag "$image" "$2:latest"
}
if tag_running_image api "$BACKEND_IMAGE_REPO" && tag_running_image frontend "$FRONTEND_IMAGE_REPO"; then
  log "Tagged $BACKEND_TAG and $FRONTEND_TAG (and latest)"
else
  log "WARNING: could not tag the running images; a rollback to $CURRENT will rebuild"
fi

prune_images() {
  local keep=" $SHA_TAG " sha short repo tag help cache_flag
  # The last KEEP_DEPLOYS distinct commits that passed a deploy or rollback.
  while read -r sha; do
    short="$(git rev-parse --verify --quiet --short=12 "${sha}^{commit}" 2>/dev/null)" || continue
    keep+="$short "
  done < <(awk '$2 == "deploy" || $2 == "rollback" { print $3 }' "$HISTORY" | tac | awk '!seen[$0]++' | head -n "$KEEP_DEPLOYS")
  for repo in "$BACKEND_IMAGE_REPO" "$FRONTEND_IMAGE_REPO"; do
    while read -r tag; do
      [[ "$tag" =~ ^[0-9a-f]{12}$ && "$keep" != *" $tag "* ]] || continue
      if docker rmi "$repo:$tag" >/dev/null; then
        log "Removed $repo:$tag"
      else
        log "WARNING: could not remove $repo:$tag"
      fi
    done < <(docker image ls --format '{{.Tag}}' "$repo" 2>/dev/null || true)
  done
  docker image prune -f || log "WARNING: docker image prune failed"
  # Docker 28+ replaced --keep-storage with --max-used-space.
  help="$(docker builder prune --help 2>/dev/null || true)"
  if [[ "$help" == *--max-used-space* ]]; then cache_flag="--max-used-space"; else cache_flag="--keep-storage"; fi
  docker builder prune -f "$cache_flag" "$BUILD_CACHE_KEEP" || log "WARNING: docker builder prune failed"
}
log "Keeping images of the last $KEEP_DEPLOYS deployed commits; pruning the rest"
prune_images

log "Deployed ($MODE). Previous: $PREVIOUS. History: deploy/.deploy-history"
git rev-parse --short HEAD
