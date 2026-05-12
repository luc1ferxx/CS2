# Observability + Diagnostics V1 Goal

## Objective

Based on the current `main` branch, implement Observability + Diagnostics v1.

Do not add OpenAI, do not implement real CS2 rendering, do not replace the dev-only owner boundary with production auth, and do not integrate Sentry, Datadog, OpenTelemetry, or another external observability platform. The goal is to add lightweight, safe diagnostics that make local and preview failures easier to understand.

## Start

1. `git pull origin main`
2. Read:
   - `AGENTS.md`
   - `README.md`
   - `docs/deployment_readiness_v1.md`
   - `docs/cloud_preview_deploy_v1.md`
   - `docs/sample_demo_fixture_v1.md`
3. Inspect:
   - `backend/app/main.py`
   - `backend/app/api/`
   - `backend/app/workers/worker.py`
   - `backend/app/services/demo_service.py`
   - `backend/app/services/storage.py`
   - `backend/app/parser/demo_parser.py`
   - `backend/app/parser/normalizer.py`
   - `backend/app/models/demo.py`
   - `backend/app/models/job.py`
   - `backend/app/schemas/demo.py`
   - `backend/tests/`
   - `frontend/app/dashboard/page.tsx`
   - `frontend/app/demos/[demoId]/page.tsx`
   - `frontend/lib/api.ts`
   - `frontend/types/demo.ts`
   - `scripts/cloud_preview_smoke.py`

## Requirements

### 1. Add safe backend diagnostics

Add a lightweight diagnostics endpoint or endpoints.

Suggested shape:

- `GET /diagnostics`
- optionally `GET /demos/{demo_id}/diagnostics`

Expose compact, safe status only:

- API status/version-ish info
- database reachability
- Redis reachability
- storage readiness
- worker dependency readiness
- recent job counts by type/status
- recent failed job summaries
- render-worker connected/not connected status if current app can infer it

Do not expose:

- secrets
- full environment dumps
- stack traces
- local absolute filesystem paths
- raw parser data
- user-uploaded file contents

### 2. Add job/demo diagnostic metadata where useful

Improve compact metadata for failed or stuck flows.

Useful examples:

- parse failure category/message
- worker last status transition
- render job last status/error
- storage key presence without leaking full local path
- media URL availability status
- parser map calibration/fallback status

Keep data compact and avoid schema churn where existing fields already work.

### 3. Add worker heartbeat or last-run signal

Add a simple way to tell whether the worker is alive or recently processed work.

Acceptable options:

- Redis heartbeat key
- database timestamp on recent worker activity
- health/diagnostics field based on queue/job state

Keep it simple. Do not add a scheduler, metrics daemon, or external service.

### 4. Improve frontend failure diagnostics lightly

Keep UI changes small and utilitarian.

Where failed/stuck states already appear, show more actionable text:

- dashboard failed demo row
- demo detail failed parse message
- render operator panel failed/queued state

Do not redesign the dashboard, demo detail, coaching panel, or round review.

### 5. Update smoke script behavior

Update `scripts/cloud_preview_smoke.py` so when smoke fails, it fetches diagnostics and prints a compact summary.

If smoke passes, it can print a short diagnostics summary if useful.

Keep output concise.

### 6. Add tests

Add focused tests for diagnostics behavior.

Required coverage:

- diagnostics endpoint redacts/omits unsafe values
- diagnostics reports DB/Redis/storage readiness in healthy path
- failed parser job appears with safe category/message
- worker heartbeat/last-run signal can be read
- smoke diagnostics helper handles endpoint unavailable gracefully

Do not add brittle browser tests unless needed.

### 7. Update docs

Update:

- `README.md`
- `AGENTS.md`
- `docs/deployment_readiness_v1.md`
- `docs/cloud_preview_deploy_v1.md`

Document:

- diagnostics endpoints
- what diagnostics do and do not expose
- how to use diagnostics during preview smoke failures
- limitations versus real production observability

## Preserve

Do not regress:

- `./scripts/verify.sh`
- cloud preview smoke
- sample demo smoke
- mock upload
- real `.dem` upload
- parser reliability v2 failure handling
- map calibration/fallback status
- Demo Library search/filter/sort
- rename/archive
- dev owner boundary
- storage boundary compatibility
- round review
- rules analyzer v3
- coaching UI
- tactical map sync
- Generate Clip / `render_clip`
- RenderOperatorPanel
- fake/manual render-worker callback flows

## Tests

Prefer backend unit/API tests and smoke-helper tests.

Avoid:

- external observability dependencies
- large fixture files
- logging secrets or absolute paths in snapshots

## Verification

Run the relevant checks before committing:

```bash
./scripts/verify.sh
docker compose build
docker compose up -d
curl http://localhost:8000/health
curl http://localhost:8000/diagnostics
python3 scripts/cloud_preview_smoke.py
```

If a local sample demo is available, also run:

```bash
SAMPLE_DEMO_PATH=/absolute/path/to/sample.dem python3 scripts/cloud_preview_smoke.py
```

Manual smoke:

- dashboard loads
- failed/corrupt demo shows useful failure reason
- demo detail opens for existing parsed demo
- render operator panel still shows job status
- diagnostics endpoint does not leak secrets or local absolute paths
- desktop/mobile views have no console errors

## Completion

Commit and push to `origin/main` with a concise imperative commit message.
