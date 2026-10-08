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

# The <sha> of the cs2coach-backend:<sha> image the api container was created
# from, or nothing (compose's default image names carry no sha).
current_backend_sha() {
  local id image
  id="$(compose ps -a -q api 2>/dev/null | head -n 1 || true)"
  [[ -n "$id" ]] || return 0
  image="$(docker inspect -f '{{.Config.Image}}' "$id" 2>/dev/null || true)"
  if [[ "$image" =~ ^cs2coach-backend:([0-9a-f]{7,40})$ ]]; then printf '%s' "${BASH_REMATCH[1]}"; fi
}

# The one compose invocation every deploy script uses.
compose() {
  docker compose --env-file "$ENV_FILE" \
    -f "$REPO_ROOT/docker-compose.yml" \
    -f "$REPO_ROOT/docker-compose.preview.yml" \
    -f "$REPO_ROOT/docker-compose.prod.yml" \
    "$@"
}

# --- Alerts and pings (ALERT_WEBHOOK_URL, DEADMAN_PING_URL, BACKUP_PING_URL) ---
# Messages carry a service name, a state and a short reason only: never an
# account, SteamID, file name, path or IP.

# watch.sh state; deploy.sh and restore.sh drop a maintenance marker here so a
# watch pass during planned restarts stays quiet.
WATCH_STATE_DIR="${CS2COACH_WATCH_STATE_DIR:-${XDG_STATE_HOME:-${HOME:-/tmp}/.local/state}/cs2coach-watch}"
MAINTENANCE_MARKER="$WATCH_STATE_DIR/maintenance"

maintenance_begin() { # REASON
  if mkdir -p "$WATCH_STATE_DIR" 2>/dev/null; then
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" >"$MAINTENANCE_MARKER" 2>/dev/null || true
  fi
}
maintenance_end() { rm -f "$MAINTENANCE_MARKER" 2>/dev/null || true; }

urlencode() { # TEXT: percent-encode every byte outside [A-Za-z0-9._~-]
  local LC_ALL=C s="$1" out="" c i
  for ((i = 0; i < ${#s}; i++)); do
    c="${s:i:1}"
    case "$c" in
      [A-Za-z0-9.~_-]) out+="$c" ;;
      *)
        printf -v c '%%%02X' "'$c"
        out+="$c"
        ;;
    esac
  done
  printf '%s' "$out"
}

# curl_secret_url URL [curl args...]: the URL goes through a curl config on
# stdin, so tokens inside it (Telegram, Server酱, ping UUIDs) stay out of `ps`.
curl_secret_url() {
  local url="$1"
  shift
  url="${url//\\/\\\\}"
  url="${url//\"/\\\"}"
  printf 'url = "%s"\n' "$url" | curl -fsS --max-time 15 --retry 2 -o /dev/null -K - "$@"
}

# send_alert LINE: one plain-text line to ALERT_WEBHOOK_URL (empty = off). The
# line is the POST body (ntfy and generic webhooks); a URL containing {message}
# gets the URL-encoded line there instead and is fetched with GET (Telegram
# sendMessage ...&text={message}, Server酱 ...send?title={message}).
send_alert() {
  local url encoded
  [[ -f "$ENV_FILE" ]] || return 0
  url="$(env_get ALERT_WEBHOOK_URL)"
  [[ -n "$url" ]] || return 0
  if [[ "$url" == *'{message}'* ]]; then
    encoded="$(urlencode "$1")"
    curl_secret_url "${url//'{message}'/"$encoded"}"
  else
    curl_secret_url "$url" -H 'Content-Type: text/plain; charset=utf-8' --data-binary "$1"
  fi
}

# ping_url KEY: GET the URL in KEY (DEADMAN_PING_URL, BACKUP_PING_URL) when set.
ping_url() {
  local url
  [[ -f "$ENV_FILE" ]] || return 0
  url="$(env_get "$1")"
  [[ -n "$url" ]] || return 0
  curl_secret_url "$url"
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
