# Storage Boundary V1 Goal

## Objective

Based on the current `main` branch, implement Storage Boundary v1.

Do not add OpenAI, do not implement real CS2 rendering, do not change the render-worker architecture, and do not add real S3/R2 credentials. The goal is to route demo uploads, replay artifacts, summaries, and rendered media through a small storage abstraction while keeping the default implementation as local filesystem storage.

## Start

1. `git pull origin main`
2. Read:
   - `AGENTS.md`
   - `README.md`
   - `docs/deployment_readiness_v1.md`
   - `docker-compose.yml`
3. Inspect:
   - `backend/app/api/uploads.py`
   - `backend/app/api/demos.py`
   - `backend/app/services/demo_service.py`
   - `backend/app/models/demo.py`
   - `backend/app/models/job.py`
   - `backend/app/schemas/demo.py`
   - `backend/app/workers/worker.py`
   - `backend/tests/`
   - `render-worker/runner.py`
   - `render-worker/adapters/`
   - `render-worker/tests/`
   - `frontend/lib/api.ts`
   - `frontend/types/demo.ts`

## Requirements

### 1. Add a backend storage abstraction

Add a small storage service interface for application artifacts.

It should support operations like:

- write/upload bytes or a file-like stream
- read/open bytes when needed
- check existence
- delete or soft-clean local artifacts when appropriate
- generate local/API-accessible URLs for media that the frontend can play

Keep the first implementation local filesystem based.

Suggested shape:

- `backend/app/services/storage.py`
- `LocalStorageService`
- storage key helpers for demos, replay blobs, summaries, manual videos, and rendered clips

Use structured APIs and safe path handling. Do not build storage keys with unchecked string concatenation.

### 2. Route existing artifact writes through storage

Move existing direct file writes/reads behind the storage service where practical.

Cover at minimum:

- uploaded `.dem` files
- replay JSON blobs or replay artifacts
- summary artifacts if present
- manual uploaded/calibrated videos
- rendered clip video outputs or callback media references

The database should continue storing compact metadata and storage keys, not large raw artifacts.

### 3. Preserve local development behavior

The app should still work with Docker Compose and local development defaults.

Requirements:

- existing uploads keep working
- existing replay/detail pages keep loading
- existing video playback URLs keep working
- old rows/artifacts remain compatible where feasible
- local storage root should be configurable by environment variable
- default storage root should be documented and sensible for Docker Compose

Do not introduce real object storage, bucket credentials, cloud SDKs, or migrations that make local development harder.

### 4. Add safety checks for untrusted uploads

Keep `.dem` and media uploads treated as untrusted.

Add or verify:

- extension checks for `.dem` uploads
- content length / size limit handling if current code lacks it
- no path traversal via uploaded filenames
- generated storage keys do not depend on raw filenames alone
- manual video paths/callbacks cannot read arbitrary local files through the API

Do not overbuild malware scanning or deep media validation in this step.

### 5. Keep render-worker contract stable

Do not redesign render-worker.

The render-worker may still use local prepared workspaces for manual operator artifacts, but API-facing media callbacks should align with the backend storage key/URL model.

Preserve:

- FakeVideoAdapter flow
- CS2ManualAdapter prepare/wait/complete flow
- manual operator status artifacts
- render callback behavior

### 6. Update docs and config

Update:

- `README.md`
- `AGENTS.md`
- `docs/deployment_readiness_v1.md`

Document:

- local storage root env var
- artifact categories and expected storage keys
- what remains local-dev only
- future S3/R2 replacement path
- why large files should stay out of PostgreSQL

## Preserve

Do not regress:

- mock upload
- real `.dem` upload
- Demo Library search/filter/sort
- rename/archive
- dev owner boundary
- round review
- parser events
- rules analyzer v3
- coaching UI
- timeline parser/coaching markers
- tactical map sync
- Generate Clip / `render_clip`
- RenderOperatorPanel
- fake/manual render-worker callback flows
- deployment readiness verification script

## Tests

Add or update focused tests.

Required backend coverage:

- storage keys reject path traversal
- uploaded demo filenames cannot escape storage root
- demo upload stores a storage key and remains retrievable
- replay artifact read/write still works through the service
- manual video/update surfaces do not accept arbitrary local paths
- owner boundary still applies to storage-backed demo/video surfaces

Update render-worker tests only if callback or artifact contract changes.

## Verification

Run the relevant checks before committing:

```bash
./scripts/verify.sh
docker compose build
docker compose up -d
curl http://localhost:8000/health
```

If `./scripts/verify.sh` is unavailable or incomplete, run the underlying checks:

```bash
python3 -m compileall backend/app
PYTHONPATH=backend python3 -m unittest discover backend/tests
python3 -m compileall render-worker
python3 -m unittest discover render-worker/tests
cd frontend && npm run lint
cd frontend && npm run typecheck
cd frontend && npm run build
```

Also manually verify:

- dashboard loads
- mock upload creates and lists a demo
- real `.dem` upload parses
- demo detail opens
- replay/map/timeline sync still works
- Generate Clip creates a render job
- fake/manual rendered media still appears through the expected URL path
- no console errors on desktop/mobile smoke checks

## Completion

Commit and push to `origin/main` with a concise imperative commit message.
