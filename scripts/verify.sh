#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

run() {
  printf '\n==> %s\n' "$*"
  "$@"
}

cd "$ROOT_DIR"

run python3 -m compileall backend/app
run env PYTHONPATH=backend python3 -m unittest discover backend/tests
run python3 -m compileall render-worker
run python3 -m unittest discover render-worker/tests

cd "$ROOT_DIR/frontend"
for helper_test in lib/*.test.mjs; do
  run node "$helper_test"
done
run npm run lint
run npm run typecheck
run npm run build
