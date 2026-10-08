#!/usr/bin/env bash
#
# Local verification for CS2 Demo AI Coach.
#
# This runs the same checks as .github/workflows/ci.yml, in the same order, so
# that "it passes locally" and "it passes in CI" mean the same thing. If you
# change one, change the other. The one local-only step is the real-demo
# manifest check at the end: CI has no .dem files.
#
# Usage:
#   scripts/verify.sh              # everything
#   PYTHON=/path/to/python scripts/verify.sh
#
# Unlike CI, this does not stop at the first failure: every check runs and a
# summary is printed at the end, so one broken thing does not hide the rest.

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR" || exit 1

# ---------------------------------------------------------------------------
# Interpreter selection
#
# This script used to hardcode `python3`, which on a Windows dev machine (and
# inside plenty of containers) resolves to a bare interpreter with none of the
# backend dependencies installed. The suite then "fails" with 22 import errors
# that have nothing to do with the code. Prefer the project virtualenv.
# ---------------------------------------------------------------------------
pick_python() {
  if [ -n "${PYTHON:-}" ]; then echo "$PYTHON"; return; fi
  for candidate in .venv/Scripts/python.exe .venv/bin/python venv/Scripts/python.exe venv/bin/python; do
    [ -x "$candidate" ] && { echo "$ROOT_DIR/$candidate"; return; }
  done
  command -v python3 >/dev/null 2>&1 && { echo python3; return; }
  command -v python  >/dev/null 2>&1 && { echo python;  return; }
  echo ""
}

PY="$(pick_python)"
if [ -z "$PY" ]; then
  echo "ERROR: no Python interpreter found. Set PYTHON=/path/to/python." >&2
  exit 1
fi

echo "Python:  $("$PY" --version 2>&1)  [$PY]"
command -v node >/dev/null 2>&1 && echo "Node:    $(node --version)"
echo

# Fail loudly and early rather than after 494 confusing import errors.
if ! "$PY" -c "import fastapi, sqlalchemy, pydantic" >/dev/null 2>&1; then
  cat >&2 <<EOF
ERROR: backend dependencies are not importable with this interpreter.

  interpreter: $PY

Install them, then re-run:

  $PY -m pip install -r backend/requirements.txt

EOF
  exit 1
fi

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
FAILED=()
PASSED=()
SKIPPED=()

run() {
  local label="$1"; shift
  printf '\n\033[1m==> %s\033[0m\n' "$label"
  if "$@"; then
    PASSED+=("$label")
  else
    FAILED+=("$label")
    printf '\033[31mFAILED: %s\033[0m\n' "$label"
  fi
}

skip() {
  SKIPPED+=("$1")
  printf '\n\033[33m==> SKIPPED: %s\033[0m\n    %s\n' "$1" "$2"
}

# ---------------------------------------------------------------------------
# Python: compile, test, lint, typecheck
# ---------------------------------------------------------------------------
run "backend compileall"        "$PY" -m compileall -q backend/app
# The real-demo manifest check has its own step below; keep it out of this one.
run "backend tests"             env -u REAL_DEMO_MANIFEST_CHECK PYTHONPATH=backend "$PY" -m unittest discover backend/tests
run "render-worker compileall"  "$PY" -m compileall -q render-worker
run "render-worker tests"       "$PY" -m unittest discover render-worker/tests

# ruff and mypy are pinned in requirements-dev.txt. They are optional locally so
# that a fresh clone can still run the test suite, but CI always enforces them.
DEV_HINT="$PY -m pip install -r requirements-dev.txt"

if "$PY" -m ruff --version >/dev/null 2>&1; then
  run "ruff check" "$PY" -m ruff check .
else
  skip "ruff check" "ruff is not installed. Install with: $DEV_HINT"
fi

if "$PY" -m mypy --version >/dev/null 2>&1; then
  run "mypy" "$PY" -m mypy
else
  skip "mypy" "mypy is not installed. Install with: $DEV_HINT"
fi

# ---------------------------------------------------------------------------
# Shell scripts and the production Compose shape
#
# CI's shell/compose job. bash -n always runs; shellcheck and docker compose
# are optional locally, so a missing tool is a skip. `compose config` renders
# the three-file production shape client-side and starts nothing.
# ---------------------------------------------------------------------------
SHELL_SCRIPTS=(scripts/*.sh scripts/deploy/*.sh)

bash_syntax() {
  local script
  local status=0
  for script in "${SHELL_SCRIPTS[@]}"; do
    bash -n "$script" || status=1
  done
  return "$status"
}

run "bash -n" bash_syntax

if command -v shellcheck >/dev/null 2>&1; then
  run "shellcheck" shellcheck -x "${SHELL_SCRIPTS[@]}"
else
  skip "shellcheck" "shellcheck is not installed (CI runs it)."
fi

if docker compose version >/dev/null 2>&1; then
  run "compose config (production shape)" docker compose \
    --env-file deploy/env.production.example \
    -f docker-compose.yml -f docker-compose.preview.yml -f docker-compose.prod.yml \
    config -q
else
  skip "compose config (production shape)" "docker compose is not available (CI runs it)."
fi

# ---------------------------------------------------------------------------
# Frontend
#
# Telemetry is off for the same reason CI turns it off: a verification run
# should not phone home, and `next build` writes its telemetry config with an
# atomic rename into the user profile. Where that path is redirected onto
# another volume, the rename fails with EXDEV and the build reports an error
# that has nothing to do with the code.
# ---------------------------------------------------------------------------
export NEXT_TELEMETRY_DISABLED=1

if ! command -v node >/dev/null 2>&1; then
  skip "frontend" "node is not installed."
elif [ ! -d frontend/node_modules ]; then
  skip "frontend" "frontend/node_modules is missing. Install with: (cd frontend && npm ci)"
else
  cd "$ROOT_DIR/frontend" || exit 1
  for helper_test in lib/*.test.mjs; do
    run "frontend $(basename "$helper_test")" node "$helper_test"
  done
  # Component and page tests: Vitest + Testing Library on jsdom, mounting the
  # real pages against a mocked API client (see frontend/vitest.config.ts).
  run "frontend component tests" npm test
  run "frontend lint"      npm run lint
  run "frontend typecheck" npm run typecheck
  run "frontend build"     npm run build
  cd "$ROOT_DIR" || exit 1
fi

# ---------------------------------------------------------------------------
# Real demos (local only)
#
# CI has no .dem files, so this step exists only here. With a .dem in the
# repository root, every one that backend/tests/fixtures/real_demo_manifest.json
# knows is parsed, normalized and analyzed like the worker does (about 10 s
# each) and compared with its aggregate entry; unknown demos are skipped. See
# docs/sample_demo_fixture_v1.md. REPLAY_V2_SAMPLE_CHECK=1 additionally runs the
# replay contract v2-v5 sample checks in the backend tests step (they parse the
# demos again).
# ---------------------------------------------------------------------------
has_root_demo() {
  local demo
  for demo in "$ROOT_DIR"/*.dem; do
    [ -f "$demo" ] && return 0
  done
  return 1
}

if has_root_demo; then
  # OPENBLAS_NUM_THREADS=1 as in the worker's parse child: numpy's OpenBLAS
  # otherwise reserves buffers for every core, and the parse never uses BLAS.
  run "real demo manifest" env PYTHONPATH=backend REAL_DEMO_MANIFEST_CHECK=1 OPENBLAS_NUM_THREADS=1 \
    "$PY" -m unittest discover -s backend/tests -p test_real_demo_manifest.py
else
  skip "real demo manifest" "no .dem in the repository root (see docs/sample_demo_fixture_v1.md)."
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
printf '\n\033[1m%s\033[0m\n' "----- summary -----"
printf 'passed:  %d\n' "${#PASSED[@]}"
printf 'skipped: %d\n' "${#SKIPPED[@]}"
printf 'failed:  %d\n' "${#FAILED[@]}"

for s in ${SKIPPED[@]+"${SKIPPED[@]}"}; do printf '\033[33m  SKIP  %s\033[0m\n' "$s"; done
for f in ${FAILED[@]+"${FAILED[@]}"};  do printf '\033[31m  FAIL  %s\033[0m\n' "$f"; done

if [ "${#FAILED[@]}" -gt 0 ]; then
  exit 1
fi

printf '\n\033[32mAll checks passed.\033[0m\n'
