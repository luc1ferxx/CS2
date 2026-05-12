# Release Candidate QA V1

This runbook defines the repeatable release-candidate validation pass for local Docker development and short-lived cloud previews. It is a QA gate for the current mock MVP, not a production launch checklist.

## Scope

RC QA validates that the demo-first review flow still works:

1. Local checks compile, test, lint, typecheck, and build.
2. Docker services build and start.
3. API health and safe diagnostics are reachable.
4. Cloud preview smoke creates a mock demo, opens replay/coaching data, creates a `render_clip` job, checks media routing, and prints diagnostics.
5. Optional sample `.dem` smoke proves fresh real-demo upload, Redis parse dispatch, replay storage, rules analysis, and map calibration/fallback metadata.
6. Manual browser smoke verifies the dense Demo Library and Demo Detail review workflows on desktop and mobile.

## Non-Goals

- No OpenAI or LLM coaching.
- No production auth, sessions, OAuth, JWT, passwords, or account UI.
- No real CS2, Steam, OBS, ffmpeg, screen recording, or local game-client control in API/worker containers.
- No object-storage migration or production media durability work.
- No checked-in `.dem`, video, replay blob, parser dump, or generated media artifacts.

## Required Local RC Checklist

Run from a clean `main` checkout unless you are intentionally validating an uncommitted candidate:

```bash
git status --short
git pull origin main
```

Then run the non-browser gates:

```bash
./scripts/verify.sh
docker compose build
docker compose up -d
curl http://localhost:8000/health
curl http://localhost:8000/diagnostics
API_BASE_URL=http://localhost:8000 FRONTEND_URL=http://localhost:3000 python3 scripts/cloud_preview_smoke.py
```

You can run the same non-browser sequence with:

```bash
./scripts/rc_check.sh
```

`scripts/rc_check.sh` does not replace manual browser QA. It intentionally stops after the automated local checks and prints the manual browser items that still need a human pass.

## Optional Sample `.dem` Validation

Keep real samples in ignored local folders such as `sample-demos/`, `samples/`, or `.local/samples/`.

```bash
export SAMPLE_DEMO_PATH="$PWD/sample-demos/sample.dem"
export SAMPLE_DEMO_NAME="Local Sample Demo"
```

Run optional sample smoke:

```bash
SAMPLE_DEMO_PATH="$SAMPLE_DEMO_PATH" python3 scripts/cloud_preview_smoke.py
```

Run strict sample smoke when RC validation must prove fresh real-demo ingestion:

```bash
SAMPLE_DEMO_PATH="$SAMPLE_DEMO_PATH" python3 scripts/cloud_preview_smoke.py --require-sample
```

Or make `scripts/rc_check.sh` fail when no sample is configured:

```bash
REQUIRE_SAMPLE_DEMO=1 SAMPLE_DEMO_PATH="$SAMPLE_DEMO_PATH" ./scripts/rc_check.sh
```

When `SAMPLE_DEMO_PATH` is absent and strict mode is not requested, sample validation is skipped with a clear message.

## Cloud Preview RC Flow

Build a hosted preview with public origins set before the frontend image is built:

```bash
export FRONTEND_URL=https://cs2-preview.example.com
export NEXT_PUBLIC_API_BASE_URL=https://cs2-api-preview.example.com
export BACKEND_PUBLIC_URL=https://cs2-api-preview.example.com
export MEDIA_URL_BASE=https://cs2-api-preview.example.com
export CORS_ORIGINS=https://cs2-preview.example.com

docker compose -f docker-compose.yml -f docker-compose.preview.yml up --build -d
```

Check the public API and run smoke against the public URLs:

```bash
curl "$NEXT_PUBLIC_API_BASE_URL/health"
curl "$NEXT_PUBLIC_API_BASE_URL/diagnostics"

API_BASE_URL="$NEXT_PUBLIC_API_BASE_URL" \
FRONTEND_URL="$FRONTEND_URL" \
python3 scripts/cloud_preview_smoke.py
```

If a sample is available for the preview environment:

```bash
SAMPLE_DEMO_PATH=/absolute/path/to/sample.dem \
API_BASE_URL="$NEXT_PUBLIC_API_BASE_URL" \
FRONTEND_URL="$FRONTEND_URL" \
python3 scripts/cloud_preview_smoke.py --require-sample
```

## Manual Browser Smoke

Open `/dashboard` in the target frontend and verify:

- Dashboard loads with no current console errors.
- Empty, loading, fetch-failed, archived-only, and search/filter no-result states are clear when practical to exercise.
- Mock upload creates a demo and the post-create notice/table action opens it.
- Real/sample `.dem` upload parses and opens when `SAMPLE_DEMO_PATH` or another approved local sample is available.
- Corrupt `.dem` failure shows compact parser metadata such as `INVALID_DEMO` without stack traces or local paths.
- Demo Library search, status/map filters, sort order, rename, archive, and show archived work.
- Demo Detail summary is readable: file, map, calibration/fallback, rounds, coaching count, parser status/failure, media status, and render status.
- Replay play/pause, seek, and speed controls work.
- Round review quick jumps update the shared tick/round state.
- Tactical map syncs with timeline/replay, and map calibration/fallback is visible.
- Timeline parser and coaching markers click-to-seek.
- Coaching severity/rule/search filters and event cards work.
- `Generate Clip` creates a `render_clip` job, and local no-GPU fallback appears as `GPU worker not connected for render_clip`.
- `RenderOperatorPanel` shows latest job status, tick range, output, and compact errors.
- A `/media/...` URL returns `200` or `206` where media exists; missing media smoke returns expected `404`.
- Desktop and mobile widths do not show incoherent horizontal overflow, clipped controls, or current console errors.

## Safe Preview Boundaries

RC sign-off must preserve these boundaries:

- `DEV_USER_ID` and `X-Dev-User-Id` are dev-only owner scoping, not production auth.
- Fake/manual render-worker flows are not real GPU rendering.
- Local filesystem storage and Docker volumes are preview/MVP storage, not durable production object storage.
- Uploaded demos are untrusted input.
- Coaching is deterministic rules output; no OpenAI/LLM workflow is in scope.
- Do not commit `.dem`, video, replay, parser dump, local storage, or generated media artifacts.

## Completion Evidence

For a release-candidate handoff, record:

- Git commit SHA and `git status --short` output.
- Non-browser command output, or `./scripts/rc_check.sh` output.
- Whether optional sample smoke was skipped, optional, or strict.
- Manual browser smoke result, including desktop/mobile viewport coverage and any known limitations.
- `/health` and `/diagnostics` status at the time of validation.
