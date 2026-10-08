#!/usr/bin/env bash
# Health watch for the production VPS, every 5 minutes (systemd:
# cs2coach-watch.timer). Run as the app user.
#
#   bash scripts/deploy/watch.sh               # one pass
#   bash scripts/deploy/watch.sh --test-alert  # send one test line to ALERT_WEBHOOK_URL
#
# Checks, each either ok or failing:
#   health, health/worker  GET /health and /health/worker through the public origin
#   docker, compose        the Docker daemon answers; the compose files resolve
#   svc:<service>          every compose service has a running container whose
#                          restart count did not grow since the last pass
#   disk:<mount>           /, Docker's data root and BACKUP_LOCAL_DIR stay below
#                          WATCH_DISK_PERCENT used (0 = off)
#   backup                 the newest local pg-*.dump is younger than
#                          WATCH_BACKUP_MAX_AGE_HOURS (0 = off)
# Only a change of state is sent to ALERT_WEBHOOK_URL, as one plain-text line
# naming the check, the state and a short reason (never an account, SteamID,
# file name or IP). A pass with every check ok GETs DEADMAN_PING_URL. A pass
# while deploy.sh or a production restore runs is skipped. State lives in
# ~/.local/state/cs2coach-watch. Exits non-zero only when an alert could not be
# sent; the same change is sent again on the next pass.
set -Eeuo pipefail
# shellcheck source=scripts/deploy/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

usage() {
  awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"
}

# A marker older than this is left over from a crashed run, not a deploy.
MAINTENANCE_MAX_AGE_SECONDS=3600

case "${1-}" in
  "") require_env_file ;;
  --test-alert)
    require_env_file
    [[ -n "$(env_get ALERT_WEBHOOK_URL)" ]] || die "ALERT_WEBHOOK_URL is empty in $ENV_FILE"
    send_alert "[cs2coach] TEST watch.sh alert channel works" || die "the alert could not be sent"
    log "Test alert sent"
    exit 0
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

SITE_DOMAIN="$(env_get SITE_DOMAIN)"
[[ "$SITE_DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || die "SITE_DOMAIN must be a bare host name such as coach.example.com"
BASE_URL="https://$SITE_DOMAIN"
BACKUP_DIR="$(env_get BACKUP_LOCAL_DIR /var/backups/cs2coach)"
DISK_PERCENT="$(env_get WATCH_DISK_PERCENT 85)"
BACKUP_MAX_AGE_HOURS="$(env_get WATCH_BACKUP_MAX_AGE_HOURS 26)"
[[ "$DISK_PERCENT" =~ ^[0-9]+$ ]] || die "WATCH_DISK_PERCENT must be a whole number"
[[ "$BACKUP_MAX_AGE_HOURS" =~ ^[0-9]+$ ]] || die "WATCH_BACKUP_MAX_AGE_HOURS must be a whole number"

mkdir -p "$WATCH_STATE_DIR"
CHECKS_FILE="$WATCH_STATE_DIR/checks"     # <key> <ok|fail>
RESTARTS_FILE="$WATCH_STATE_DIR/restarts" # <service> <container id> <restart count>

if [[ -f "$MAINTENANCE_MARKER" ]]; then
  marker_age=$(($(date +%s) - $(stat -c %Y "$MAINTENANCE_MARKER" 2>/dev/null || echo 0)))
  if ((marker_age < MAINTENANCE_MAX_AGE_SECONDS)); then
    log "Skipping this pass: $(cat "$MAINTENANCE_MARKER" 2>/dev/null || echo maintenance) in progress"
    exit 0
  fi
  log "Ignoring a maintenance marker ${marker_age}s old"
fi

declare -A STATUS=() REASON=() PREV=() PREV_RESTART=()
ORDER=()
record() { # KEY ok|fail [REASON]
  [[ -n "${STATUS[$1]+set}" ]] || ORDER+=("$1")
  STATUS[$1]="$2"
  REASON[$1]="${3-}"
}

if [[ -f "$CHECKS_FILE" ]]; then
  while read -r key state; do
    if [[ -n "$key" ]]; then PREV[$key]="$state"; fi
  done <"$CHECKS_FILE"
fi
if [[ -f "$RESTARTS_FILE" ]]; then
  while read -r svc id count; do
    if [[ -n "$svc" ]]; then PREV_RESTART[$svc]="$id $count"; fi
  done <"$RESTARTS_FILE"
fi

# --- HTTP through Caddy -------------------------------------------------------
check_http() { # KEY PATH
  local code
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "$BASE_URL$2" 2>/dev/null || true)"
  if [[ "$code" == "200" ]]; then
    record "$1" ok
  else
    record "$1" fail "GET $2 -> HTTP ${code:-000}"
  fi
}
check_http health /health
check_http health/worker /health/worker

# --- Containers -----------------------------------------------------------------
NEW_RESTARTS=()
check_containers() {
  local services svc id state count prev_id prev_count
  local -a ids=()
  local -A current=()
  services="$(compose config --services 2>/dev/null)" || {
    record compose fail "compose config failed (env file or compose files)"
    return 0
  }
  record compose ok
  mapfile -t ids < <(compose ps -a -q 2>/dev/null || true)
  if ((${#ids[@]})); then
    while read -r svc id state count; do
      if [[ -n "$svc" ]]; then current[$svc]="$id $state $count"; fi
    done < <(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}} {{.Id}} {{.State.Status}} {{.RestartCount}}' "${ids[@]}" 2>/dev/null || true)
  fi
  for svc in $services; do
    if [[ -z "${current[$svc]+set}" ]]; then
      record "svc:$svc" fail "no container"
      continue
    fi
    read -r id state count <<<"${current[$svc]}"
    NEW_RESTARTS+=("$svc $id $count")
    read -r prev_id prev_count <<<"${PREV_RESTART[$svc]-}"
    if [[ "$state" != "running" ]]; then
      record "svc:$svc" fail "container $state, restarted $count times"
    elif [[ "$prev_id" == "$id" && "$count" =~ ^[0-9]+$ && "$prev_count" =~ ^[0-9]+$ ]] && ((count > prev_count)); then
      record "svc:$svc" fail "restarted $((count - prev_count)) times since the last check"
    else
      # Same container and no new restarts, or a new container (a deploy).
      record "svc:$svc" ok
    fi
  done
}
if docker info >/dev/null 2>&1; then
  record docker ok
  check_containers
else
  record docker fail "docker daemon not answering"
fi

# --- Disk -----------------------------------------------------------------------
check_disks() {
  local dir mount pct root
  local -a dirs=(/)
  local -A seen=()
  root="$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || true)"
  [[ -z "$root" ]] || dirs+=("$root")
  [[ ! -d "$BACKUP_DIR" ]] || dirs+=("$BACKUP_DIR")
  for dir in "${dirs[@]}"; do
    # Percentage first: read leaves the rest of the line, spaces included, to the mount point.
    read -r pct mount < <(df --output=pcent,target "$dir" 2>/dev/null | tail -n +2) || continue
    pct="${pct%\%}"
    [[ -n "$mount" && "$pct" =~ ^[0-9]+$ && -z "${seen[$mount]+set}" ]] || continue
    seen[$mount]=1
    if ((pct >= DISK_PERCENT)); then
      record "disk:$mount" fail "${pct}% used (limit ${DISK_PERCENT}%)"
    else
      record "disk:$mount" ok
    fi
  done
}
if ((DISK_PERCENT > 0)); then check_disks; fi

# --- Backups --------------------------------------------------------------------
check_backup() {
  local newest age_h
  newest="$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'pg-*.dump' -printf '%T@\n' 2>/dev/null | sort -n | tail -n 1 || true)"
  if [[ -z "$newest" ]]; then
    record backup fail "no local database dump yet (is cs2coach-backup.timer enabled?)"
    return 0
  fi
  age_h=$((($(date +%s) - ${newest%.*}) / 3600))
  if ((age_h >= BACKUP_MAX_AGE_HOURS)); then
    record backup fail "newest database dump is ${age_h}h old"
  else
    record backup ok
  fi
}
if ((BACKUP_MAX_AGE_HOURS > 0)); then check_backup; fi

# --- Report, alert on changes, persist ----------------------------------------
changes=()
all_ok=1
for key in "${ORDER[@]}"; do
  state="${STATUS[$key]}"
  if [[ "$state" == "ok" ]]; then
    log "ok    $key"
    [[ "${PREV[$key]-ok}" != "fail" ]] || changes+=("OK $key recovered")
  else
    all_ok=0
    log "FAIL  $key: ${REASON[$key]}"
    [[ "${PREV[$key]-ok}" == "fail" ]] || changes+=("FAIL $key: ${REASON[$key]}")
  fi
done

if ((${#changes[@]})); then
  line="[cs2coach] $(printf '%s; ' "${changes[@]}")"
  line="${line%; }"
  if [[ -z "$(env_get ALERT_WEBHOOK_URL)" ]]; then
    log "No ALERT_WEBHOOK_URL; would have sent: $line"
  elif send_alert "$line"; then
    log "Alert sent: $line"
  else
    die "could not send the alert; state kept so the next pass sends it again: $line"
  fi
fi

# Checks this pass did not run (Docker down, a check switched off) keep their
# previous state, so a recovery is still reported once they run again.
for key in "${!STATUS[@]}"; do PREV[$key]="${STATUS[$key]}"; done
tmp="$(mktemp "$WATCH_STATE_DIR/.checks.XXXXXX")"
for key in "${!PREV[@]}"; do printf '%s %s\n' "$key" "${PREV[$key]}"; done | LC_ALL=C sort >"$tmp"
mv -f "$tmp" "$CHECKS_FILE"
if ((${#NEW_RESTARTS[@]})); then
  tmp="$(mktemp "$WATCH_STATE_DIR/.restarts.XXXXXX")"
  printf '%s\n' "${NEW_RESTARTS[@]}" >"$tmp"
  mv -f "$tmp" "$RESTARTS_FILE"
fi

if ((all_ok)); then
  ping_url DEADMAN_PING_URL || log "WARNING: DEADMAN_PING_URL did not answer"
fi
