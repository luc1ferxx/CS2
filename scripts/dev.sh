#!/usr/bin/env bash
# Compatible with the Bash 3.2 shipped on macOS.
set -euo pipefail

DEV_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV_ACTION="${1:-up}"

usage() {
  printf '%s\n' \
    'Usage: bash scripts/dev.sh [up|check|stop|logs]' \
    '  up     Build and start local development services (default).' \
    '  check  Check Docker and Compose configuration without starting services.' \
    '  stop   Stop this development stack; keep its data.' \
    '  logs   Show the last 100 log lines per service.' \
    'Uses project cs2-dev, .env.example, and local-only ports 3000/8000.' \
    'Real CS2 video generation stays on the separate Windows renderer.'
}

if [[ "$#" -gt 1 ]]; then
  usage >&2
  exit 2
fi
case "$DEV_ACTION" in
  up|check|stop|logs) ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

if command -v docker >/dev/null 2>&1; then
  DEV_DOCKER="$(command -v docker)"
elif [[ -x /Applications/Docker.app/Contents/Resources/bin/docker ]]; then
  DEV_DOCKER=/Applications/Docker.app/Contents/Resources/bin/docker
else
  printf '%s\n' 'Docker is missing. Install and start Docker Desktop, then retry.' >&2
  exit 1
fi
# Docker Desktop's credential helper lives beside the CLI on macOS.
export PATH="$(dirname "$DEV_DOCKER"):$PATH"
if ! "$DEV_DOCKER" compose version >/dev/null 2>&1; then
  printf '%s\n' 'Docker Compose is missing. Update Docker Desktop and retry.' >&2
  exit 1
fi

DEV_COMPOSE=("$DEV_DOCKER" compose --project-name cs2-dev --env-file "$DEV_ROOT/.env.example"
  -f "$DEV_ROOT/docker-compose.yml" -f "$DEV_ROOT/docker-compose.dev.yml")

if ! "${DEV_COMPOSE[@]}" config --quiet; then
  printf '%s\n' 'Invalid development configuration. !override needs Docker Compose 2.24.4 or newer.' >&2
  exit 1
fi
if ! "$DEV_DOCKER" info >/dev/null 2>&1; then
  printf '%s\n' 'Docker is not ready. Start Docker Desktop and wait for its engine, then retry.' >&2
  exit 1
fi

case "$DEV_ACTION" in
  check)
    printf '%s\n' 'Docker and development configuration are ready. No services were started.'
    ;;
  stop)
    "${DEV_COMPOSE[@]}" stop
    ;;
  logs)
    "${DEV_COMPOSE[@]}" logs --tail 100
    ;;
  up)
    if ! command -v curl >/dev/null 2>&1; then
      printf '%s\n' 'curl is required for the frontend readiness check.' >&2
      exit 1
    fi
    # Recreate the development stack so worker and dependency changes apply.
    if ! "${DEV_COMPOSE[@]}" up -d --build --force-recreate --wait --wait-timeout 180; then
      printf '%s\n' 'Startup failed. Check that ports 3000/8000 are free and run: bash scripts/dev.sh logs' >&2
      exit 1
    fi
    for ((dev_attempt = 1; dev_attempt <= 30; dev_attempt++)); do
      if curl --fail --silent --output /dev/null --max-time 5 http://localhost:3000/dashboard; then
        printf '%s\n' 'Ready: http://localhost:3000/dashboard' \
          'Import a .dem to review xelex. Video generation is unavailable in this development profile.'
        exit 0
      fi
      sleep 2
    done
    printf '%s\n' 'Services started, but the frontend is not ready. Run: bash scripts/dev.sh logs' >&2
    exit 1
    ;;
esac
