#!/usr/bin/env bash
# Build and (re)start the production stack on the VPS, wait for health through
# Caddy, then run the anonymous production smoke.
#
#   bash scripts/deploy/deploy.sh                  # fast-forward DEPLOY_BRANCH and deploy it
#   bash scripts/deploy/deploy.sh --rollback <sha> # check out <sha> (detached) and deploy it
#
# Run as the app user (member of the docker group), not root. Rollback swaps
# code only: the schema is created forward-only at API startup, so a rollback
# across a database schema change can fail or misbehave; restore a pre-deploy
# dump (scripts/deploy/restore.sh) in that case.
set -Eeuo pipefail
# shellcheck source=scripts/deploy/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

usage() {
  sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

ROLLBACK_SHA=""
while (($#)); do
  case "$1" in
    --rollback)
      [[ $# -ge 2 && -n "$2" ]] || { usage >&2; exit 2; }
      ROLLBACK_SHA="$2"
      shift 2
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

SITE_DOMAIN="$(env_get SITE_DOMAIN)"
[[ "$SITE_DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || die "SITE_DOMAIN must be a bare host name such as coach.example.com"
BASE_URL="https://$SITE_DOMAIN"
BRANCH="$(env_get DEPLOY_BRANCH main)"
HEALTH_TIMEOUT="$(env_get DEPLOY_HEALTH_TIMEOUT_SECONDS 300)"
[[ "$HEALTH_TIMEOUT" =~ ^[0-9]+$ ]] || die "DEPLOY_HEALTH_TIMEOUT_SECONDS must be a whole number"

cd "$REPO_ROOT"
if ! git diff --quiet || ! git diff --cached --quiet; then
  die "the checkout has local changes to tracked files; deploy only committed code"
fi
PREVIOUS="$(git rev-parse --short HEAD)"

if [[ -n "$ROLLBACK_SHA" ]]; then
  MODE="rollback"
  log "Rolling back from $PREVIOUS to $ROLLBACK_SHA"
  log "NOTE: code only. If a newer deploy changed the database schema, this rollback may fail; see docs/vps_deploy_v1.md."
  if ! git cat-file -e "${ROLLBACK_SHA}^{commit}" 2>/dev/null; then
    git fetch --quiet origin
  fi
  git cat-file -e "${ROLLBACK_SHA}^{commit}" 2>/dev/null || die "unknown commit: $ROLLBACK_SHA"
  git checkout --quiet --detach "$ROLLBACK_SHA"
else
  MODE="deploy"
  log "Updating $BRANCH (currently $PREVIOUS)"
  git checkout --quiet "$BRANCH"
  git pull --ff-only --quiet origin "$BRANCH"
fi
CURRENT="$(git rev-parse --short HEAD)"

# What to do after a failure once services may have changed. A first deploy
# has nothing older to roll back to.
next_step() {
  if [[ "$PREVIOUS" == "$CURRENT" ]]; then
    printf 'fix the cause (often a value in %s) and rerun: bash scripts/deploy/deploy.sh' "$ENV_FILE"
  else
    printf 'fix forward, or roll back with: bash scripts/deploy/deploy.sh --rollback %s' "$PREVIOUS"
  fi
}

log "Validating deploy/Caddyfile"
compose run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile \
  || die "deploy/Caddyfile is invalid; running services were not changed"
log "Building images for $CURRENT"
compose build --pull || die "image build failed; running services were not changed (checkout is at $CURRENT, was $PREVIOUS)"
log "Starting services"
# caddy and frontend wait for the api healthcheck, so an api that crashes on
# startup (e.g. config validation) fails here rather than in the health wait.
if ! compose up -d --remove-orphans; then
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

if ! bash "$REPO_ROOT/scripts/deploy/prod_smoke.sh" "$BASE_URL"; then
  die "production smoke failed; inspect with 'docker compose ... logs', then $(next_step)"
fi

printf '%s %s %s previous=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$MODE" "$CURRENT" "$PREVIOUS" >>"$REPO_ROOT/deploy/.deploy-history"
log "Deployed ($MODE). Previous: $PREVIOUS. History: deploy/.deploy-history"
git rev-parse --short HEAD
