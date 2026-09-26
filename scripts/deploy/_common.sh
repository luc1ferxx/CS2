# shellcheck shell=bash
# Shared helpers for scripts/deploy/*.sh. Source it; do not run it.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${CS2COACH_ENV_FILE:-$REPO_ROOT/deploy/.env.production}"

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

require_env_file() {
  [[ -f "$ENV_FILE" ]] || die "missing $ENV_FILE (cp deploy/env.production.example deploy/.env.production, then fill it in)"
}

# env_get KEY [DEFAULT]: read KEY from the env file without executing it, so
# values such as * or & stay literal. Last assignment wins; surrounding quotes,
# a trailing CR and an unquoted " # comment" are stripped.
env_get() {
  local key="$1" default="${2-}" line value
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$ENV_FILE" | tail -n 1 || true)"
  value="${line#*=}"
  value="${value%$'\r'}"
  if [[ "$value" =~ ^\"(.*)\"$ || "$value" =~ ^\'(.*)\'$ ]]; then
    value="${BASH_REMATCH[1]}"
  else
    value="${value%%[[:space:]]#*}"
  fi
  [[ -n "$value" ]] || value="$default"
  printf '%s' "$value"
}

# The one compose invocation every deploy script uses.
compose() {
  docker compose --env-file "$ENV_FILE" \
    -f "$REPO_ROOT/docker-compose.yml" \
    -f "$REPO_ROOT/docker-compose.preview.yml" \
    -f "$REPO_ROOT/docker-compose.prod.yml" \
    "$@"
}

# Defines two rclone remotes through env vars (no rclone.conf on disk):
#   cs2backup:    the backup bucket (BACKUP_* credentials)
#   cs2artifacts: the live artifact bucket (OBJECT_STORAGE_* credentials)
# Sets BACKUP_BUCKET and ARTIFACT_BUCKET.
configure_rclone_remotes() {
  command -v rclone >/dev/null 2>&1 || die "rclone is not installed (scripts/deploy/bootstrap.sh installs it)"
  local provider
  provider="$(env_get BACKUP_RCLONE_PROVIDER Cloudflare)"
  BACKUP_BUCKET="$(env_get BACKUP_BUCKET)"
  ARTIFACT_BUCKET="$(env_get OBJECT_STORAGE_BUCKET)"
  [[ -n "$BACKUP_BUCKET" ]] || die "BACKUP_BUCKET is not set in $ENV_FILE"
  [[ "$BACKUP_BUCKET" != "$ARTIFACT_BUCKET" ]] || die "BACKUP_BUCKET must be a different bucket from OBJECT_STORAGE_BUCKET"

  export RCLONE_CONFIG_CS2BACKUP_TYPE=s3
  export RCLONE_CONFIG_CS2BACKUP_PROVIDER="$provider"
  RCLONE_CONFIG_CS2BACKUP_ENDPOINT="$(env_get BACKUP_S3_ENDPOINT)"
  RCLONE_CONFIG_CS2BACKUP_REGION="$(env_get BACKUP_S3_REGION auto)"
  RCLONE_CONFIG_CS2BACKUP_ACCESS_KEY_ID="$(env_get BACKUP_S3_ACCESS_KEY_ID)"
  RCLONE_CONFIG_CS2BACKUP_SECRET_ACCESS_KEY="$(env_get BACKUP_S3_SECRET_ACCESS_KEY)"
  export RCLONE_CONFIG_CS2BACKUP_ENDPOINT RCLONE_CONFIG_CS2BACKUP_REGION
  export RCLONE_CONFIG_CS2BACKUP_ACCESS_KEY_ID RCLONE_CONFIG_CS2BACKUP_SECRET_ACCESS_KEY
  export RCLONE_CONFIG_CS2BACKUP_ACL=private
  # Bucket-scoped tokens cannot create or list buckets.
  export RCLONE_CONFIG_CS2BACKUP_NO_CHECK_BUCKET=true

  export RCLONE_CONFIG_CS2ARTIFACTS_TYPE=s3
  export RCLONE_CONFIG_CS2ARTIFACTS_PROVIDER="$provider"
  RCLONE_CONFIG_CS2ARTIFACTS_ENDPOINT="$(env_get OBJECT_STORAGE_ENDPOINT_URL)"
  RCLONE_CONFIG_CS2ARTIFACTS_REGION="$(env_get OBJECT_STORAGE_REGION auto)"
  RCLONE_CONFIG_CS2ARTIFACTS_ACCESS_KEY_ID="$(env_get OBJECT_STORAGE_ACCESS_KEY_ID)"
  RCLONE_CONFIG_CS2ARTIFACTS_SECRET_ACCESS_KEY="$(env_get OBJECT_STORAGE_SECRET_ACCESS_KEY)"
  export RCLONE_CONFIG_CS2ARTIFACTS_ENDPOINT RCLONE_CONFIG_CS2ARTIFACTS_REGION
  export RCLONE_CONFIG_CS2ARTIFACTS_ACCESS_KEY_ID RCLONE_CONFIG_CS2ARTIFACTS_SECRET_ACCESS_KEY
  export RCLONE_CONFIG_CS2ARTIFACTS_NO_CHECK_BUCKET=true
  if [[ -z "$RCLONE_CONFIG_CS2ARTIFACTS_ACCESS_KEY_ID" ]]; then
    export RCLONE_CONFIG_CS2ARTIFACTS_ENV_AUTH=true
  fi
}
