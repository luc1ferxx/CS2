#!/usr/bin/env bash
set -euo pipefail

# Development-mode RC gate: it starts the local Compose stack and requires
# GET /diagnostics, which a production API answers with 404. For a hosted
# production preview (docker-compose.preview.yml sets AUTH_MODE=production), run
# scripts/cloud_preview_smoke.py directly with AUTH_SESSION_COOKIE (a signed-in
# __Host-cs2_session value) and SAMPLE_DEMO_PATH; it reads /auth/me capabilities
# and skips the mock upload and render_clip steps production hides.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:3000}"
SAMPLE_DEMO_PATH="${SAMPLE_DEMO_PATH:-}"
REQUIRE_SAMPLE_DEMO="${REQUIRE_SAMPLE_DEMO:-${SAMPLE_DEMO_REQUIRED:-0}}"

run() {
  printf '\n==> %s\n' "$*"
  "$@"
}

wait_for_url() {
  local url="$1"
  local label="$2"
  local attempts="${3:-30}"
  local delay_seconds="${4:-2}"

  printf '\n==> waiting for %s: %s\n' "$label" "$url"
  for ((attempt = 1; attempt <= attempts; attempt += 1)); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      printf '%s ready\n' "$label"
      return 0
    fi
    sleep "$delay_seconds"
  done

  printf '%s was not ready after %s attempts: %s\n' "$label" "$attempts" "$url" >&2
  return 1
}

# /health answers 200 {"status":"ok"}, or 503 {"status":"degraded"} while a
# database, Redis or worker-configuration check fails. Waiting covers startup;
# a stack still degraded at the end is reported as degraded, not unreachable.
wait_for_health() {
  local url="$1"
  local attempts="${2:-30}"
  local delay_seconds="${3:-2}"
  local response=""
  local code=""
  local body=""

  printf '\n==> waiting for api health: %s\n' "$url"
  for ((attempt = 1; attempt <= attempts; attempt += 1)); do
    response="$(curl -sS --max-time 5 -w '\n%{http_code}' "$url" 2>/dev/null || true)"
    code="${response##*$'\n'}"
    body="${response%$'\n'*}"
    if [ "$code" = "200" ] && [[ "$body" == *'"status":"ok"'* ]]; then
      printf 'api health ok: %s\n' "$body"
      return 0
    fi
    sleep "$delay_seconds"
  done

  if [ "$code" = "503" ]; then
    printf 'api health is degraded (HTTP 503 %s): the database, Redis or worker configuration check failed; inspect "docker compose logs api" and %s/diagnostics\n' \
      "$body" "${url%/health}" >&2
  else
    printf 'api health was not ready after %s attempts: %s (last HTTP status %s)\n' \
      "$attempts" "$url" "${code:-none}" >&2
  fi
  return 1
}

truthy() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|y|on) return 0 ;;
    *) return 1 ;;
  esac
}

# The interpreter verify.sh picks: the manifest check needs the backend
# dependencies, which a bare python3 usually lacks.
backend_python() {
  local candidate
  if [ -n "${PYTHON:-}" ]; then
    printf '%s\n' "$PYTHON"
    return 0
  fi
  for candidate in .venv/Scripts/python.exe .venv/bin/python venv/Scripts/python.exe venv/bin/python; do
    if [ -x "$ROOT_DIR/$candidate" ]; then
      printf '%s\n' "$ROOT_DIR/$candidate"
      return 0
    fi
  done
  command -v python3 || command -v python
}

# Offline, before the upload: parse the sample like the worker does and compare
# it with its aggregate entry in backend/tests/fixtures/real_demo_manifest.json.
# A sample the manifest does not know is skipped (exit 0); a known one that no
# longer matches fails the gate. verify.sh already checked the repo-root demos,
# and a missing sample file is left to cloud_preview_smoke.py to report.
check_sample_manifest() {
  local sample="$1"
  [ -f "$sample" ] || return 0
  if [ "$(cd "$(dirname "$sample")" && pwd)" = "$ROOT_DIR" ] && [[ "$sample" == *.dem ]]; then
    printf '\n==> real-demo manifest: %s was checked by verify.sh\n' "$(basename "$sample")"
    return 0
  fi
  run env PYTHONPATH="$ROOT_DIR/backend" OPENBLAS_NUM_THREADS=1 \
    "$(backend_python)" -m app.cli.real_demo_manifest check "$sample"
}

docker_cmd() {
  if command -v docker >/dev/null 2>&1; then
    command -v docker
    return 0
  fi

  if [ -x /Applications/Docker.app/Contents/Resources/bin/docker ]; then
    printf '%s\n' /Applications/Docker.app/Contents/Resources/bin/docker
    return 0
  fi

  printf 'docker command not found. Start Docker Desktop or install Docker CLI.\n' >&2
  return 1
}

main() {
  cd "$ROOT_DIR"

  DOCKER_BIN="$(docker_cmd)"
  DOCKER_BIN_DIR="$(dirname "$DOCKER_BIN")"
  export PATH="$DOCKER_BIN_DIR:$PATH"

  if truthy "$REQUIRE_SAMPLE_DEMO" && [ -z "$SAMPLE_DEMO_PATH" ]; then
    printf '\nSAMPLE_DEMO_PATH is required when REQUIRE_SAMPLE_DEMO=1.\n' >&2
    exit 1
  fi

  run ./scripts/verify.sh
  run "$DOCKER_BIN" compose build
  run "$DOCKER_BIN" compose up -d
  wait_for_health "$API_BASE_URL/health"
  wait_for_url "$API_BASE_URL/diagnostics" "api diagnostics"
  run curl -fsS "$API_BASE_URL/diagnostics"
  wait_for_url "$FRONTEND_URL/dashboard" "frontend dashboard"

  run env \
    API_BASE_URL="$API_BASE_URL" \
    FRONTEND_URL="$FRONTEND_URL" \
    python3 scripts/cloud_preview_smoke.py

  if [ -n "$SAMPLE_DEMO_PATH" ]; then
    check_sample_manifest "$SAMPLE_DEMO_PATH"
    sample_args=()
    if truthy "$REQUIRE_SAMPLE_DEMO"; then
      sample_args+=(--require-sample)
    fi
    run env \
      API_BASE_URL="$API_BASE_URL" \
      FRONTEND_URL="$FRONTEND_URL" \
      SAMPLE_DEMO_PATH="$SAMPLE_DEMO_PATH" \
      SAMPLE_DEMO_NAME="${SAMPLE_DEMO_NAME:-}" \
      python3 scripts/cloud_preview_smoke.py "${sample_args[@]}"
  else
    printf '\n==> sample demo smoke skipped; set SAMPLE_DEMO_PATH to include it\n'
  fi

  cat <<'EOF'

==> manual browser QA still required
Open the target /dashboard and complete docs/release_candidate_qa_v1.md:
- dashboard states, mock upload/open, real/sample upload when configured
- corrupt demo failure copy
- search/filter/sort/rename/archive
- detail summary, replay controls, round jumps, tactical map sync, timeline markers
- coaching filters/cards, Generate Clip fallback, RenderOperatorPanel
- desktop and mobile with no current console errors
EOF
}

# Sourcing the script (the tests do) defines the helpers without running the gate.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
