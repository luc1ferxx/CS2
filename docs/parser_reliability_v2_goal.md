# Parser Reliability V2 Goal

## Objective

Based on the current `main` branch, implement Parser Reliability v2.

Do not add OpenAI, do not implement real CS2 rendering, do not change the render-worker architecture, and do not store huge raw parser dataframes or event dumps in PostgreSQL. The goal is to make real `.dem` parsing more robust, diagnosable, and backward compatible across good, partial, malformed, and unsupported demo inputs.

## Start

1. `git pull origin main`
2. Read:
   - `AGENTS.md`
   - `README.md`
   - `docs/deployment_readiness_v1.md`
   - `docs/storage_boundary_v1_goal.md`
   - `docs/cloud_preview_deploy_v1.md`
3. Inspect:
   - `backend/app/parser/demo_parser.py`
   - `backend/app/parser/normalizer.py`
   - `backend/app/parser/map_config.py`
   - `backend/app/analysis/analyzer.py`
   - `backend/app/analysis/rules.py`
   - `backend/app/services/demo_service.py`
   - `backend/app/services/storage.py`
   - `backend/app/workers/worker.py`
   - `backend/app/api/uploads.py`
   - `backend/app/api/demos.py`
   - `backend/app/schemas/demo.py`
   - `backend/tests/`
   - `frontend/types/replay.ts`
   - `frontend/types/coaching.ts`
   - `frontend/lib/replay-events.ts`
   - `frontend/lib/coaching-review.ts`
   - `frontend/app/demos/[demoId]/page.tsx`

## Requirements

### 1. Add parser error classification

Improve parse failure reporting so worker/API/UI can distinguish common cases.

At minimum classify:

- invalid or unreadable `.dem`
- unsupported parser format/version
- missing essential match metadata
- missing frames/ticks
- missing optional event family
- normalization failure
- storage/read failure
- unexpected parser exception

Keep messages concise and safe for users. Do not include sensitive local filesystem paths in public API responses.

### 2. Support partial parse results

When a demo can produce a useful replay contract but some optional data is missing, prefer a partial success over a hard failure.

Rules:

- Missing optional event families should not fail the whole parse.
- Missing `events` should load as `events: []`.
- Missing utility/bomb/kill events should cause analyzer rules that need them to skip gracefully.
- Missing or unsupported map config should use an explicit fallback map state, not Dust2 transforms.
- Old replay blobs should remain loadable.

### 3. Strengthen replay contract validation

Add a compact validation/sanitization layer for replay output before storing it.

Validate or repair:

- tick rate
- total ticks
- rounds sorted by tick
- frames sorted by tick
- player IDs/names are stable enough for UI display
- map name/display/fallback metadata
- events list shape
- parser markers shape
- frame coordinates remain finite numbers

Do not over-normalize away useful data. Prefer dropping clearly invalid small fields over failing the entire parse where safe.

### 4. Improve worker failure behavior

The worker should fail a bad parse job clearly without taking down the worker process.

Requirements:

- bad `.dem` marks demo/job failed with a useful reason
- worker continues processing later jobs
- retry behavior remains sane
- error messages are persisted compactly
- logs contain enough detail for developers
- API responses do not leak internal stack traces

### 5. Add regression fixtures or fixture builders

Add focused backend tests for parser reliability.

Use small fixtures, generated test data, mocks, or minimal corrupt files. Do not commit large real demo files.

Required coverage:

- corrupt/invalid demo fails gracefully
- parser exception is classified and persisted
- missing optional event families still produces replay with `events: []` or partial events
- old replay blob without `events` still loads
- unsupported/unknown map does not use Dust2 transform silently
- analyzer skips v3 event-driven rules when event families are missing
- worker survives parse failure and can process another job

### 6. Improve user-facing status clarity

Expose parser status and failure reason clearly enough for Dashboard/Demo Detail.

Keep UI changes small:

- show a concise parse failure reason where failed demos already show status/error text
- avoid a broad redesign
- preserve current Demo Library and Demo Detail layout

### 7. Update docs

Update:

- `README.md`
- `AGENTS.md`
- any relevant parser/deployment docs

Document:

- parser reliability expectations
- partial parse behavior
- unsupported map fallback behavior
- event-family best-effort behavior
- why raw parser dumps are not stored

## Preserve

Do not regress:

- mock upload
- real `.dem` upload
- Demo Library search/filter/sort
- rename/archive
- dev owner boundary
- storage boundary compatibility
- cloud preview deploy docs/config
- round review
- parser event markers
- rules analyzer v3
- coaching UI
- timeline parser/coaching markers
- tactical map sync
- Generate Clip / `render_clip`
- RenderOperatorPanel
- fake/manual render-worker callback flows
- `./scripts/verify.sh`

## Tests

Add focused backend tests for parser/worker reliability and frontend helper tests only if replay/coaching helper behavior changes.

Do not add large binary fixtures.

Prefer:

- mocked parser outputs
- tiny invalid binary/text files with `.dem` extension
- direct normalizer contract tests
- worker tests with controlled failing jobs
- analyzer tests with replay contracts missing event families

## Verification

Run the relevant checks before committing:

```bash
./scripts/verify.sh
docker compose build
docker compose up -d
curl http://localhost:8000/health
```

Also manually verify locally:

- dashboard loads
- mock upload still works
- real `.dem` upload still parses if a sample is available
- corrupt `.dem` upload fails with a clear status/error
- failed demo does not break dashboard/detail navigation
- existing parsed demo detail opens
- replay/map/timeline sync still works
- coaching click-to-seek still works
- Generate Clip still creates a render job

## Completion

Commit and push to `origin/main` with a concise imperative commit message.

