# Deployment Readiness V1 Goal

## Objective

Based on the current `main` branch, implement Deployment Readiness v1.

Do not add OpenAI, do not implement real CS2 rendering, do not change the render-worker architecture, and do not replace the dev-only owner boundary with production auth. The goal is to make the current mock MVP easier to deploy, verify, and demo from a clean environment.

## Start

1. `git pull origin main`
2. Read:
   - `AGENTS.md`
   - `README.md`
   - `docker-compose.yml`
3. Inspect:
   - `backend/app/main.py`
   - `backend/app/core/config.py` if present
   - `backend/app/api/`
   - `backend/app/workers/worker.py`
   - `render-worker/README.md`
   - `render-worker/runner.py`
   - `frontend/package.json`
   - `frontend/next.config.*`
   - `frontend/lib/api.ts`
   - existing docs in `docs/`

## Requirements

### 1. Document runtime configuration

Create a clear environment variable reference for local and deployment use.

Include at least:

- backend API URL / frontend public API URL
- database URL
- Redis URL
- upload/demo storage paths
- dev owner settings such as `DEV_USER_ID`
- mock/fake render settings
- render-worker callback/API settings
- any frontend `NEXT_PUBLIC_*` values

Prefer updating `README.md` and adding a focused doc such as `docs/deployment_readiness_v1.md` if useful.

### 2. Improve startup and health clarity

Keep the existing `/health` endpoint, but improve documentation or response details if the current surface is too vague.

The stack should make it clear whether:

- API is running
- database is reachable
- Redis is reachable
- worker dependencies are configured

Do not overbuild a monitoring system.

### 3. Add a one-command verification script

Add a small script or documented command that runs the normal local checks in a predictable order.

Acceptable options:

- root `scripts/verify.sh`
- root `Makefile`
- documented shell command in README

The verification should cover:

- backend compile
- backend unit tests
- render-worker compile
- render-worker unit tests
- frontend lint
- frontend typecheck
- frontend build

Keep it simple and compatible with macOS local development.

### 4. Clarify local vs deployable boundaries

Document what is ready for deployment and what remains mock/dev-only.

Be explicit that:

- dev owner boundary is not production auth
- render-worker fake/manual adapters are not real GPU rendering
- local file storage is acceptable for MVP/dev, not final production object storage
- `.dem` uploads are untrusted inputs
- real first-person CS2 rendering still belongs to an external controlled worker

### 5. Tighten Docker/production assumptions

Review `docker-compose.yml` and any Dockerfiles or startup commands.

Make small, safe changes only if needed:

- ensure services have sensible health/startup dependencies
- avoid hardcoded localhost values where service names are needed inside Docker
- keep dev defaults easy to run
- avoid introducing secrets

Do not migrate to Kubernetes, Terraform, Vercel, Fly.io, or any real cloud provider in this step.

### 6. Add deploy smoke guidance

Document a minimal smoke test checklist:

- start stack
- call `/health`
- open dashboard
- mock upload
- real `.dem` upload if sample is available
- open demo detail
- use round review
- click Generate Clip
- confirm render job status appears

This can be in README or a focused deployment/readiness doc.

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

## Verification

Run the relevant checks before committing:

```bash
python3 -m compileall backend/app
PYTHONPATH=backend python3 -m unittest discover backend/tests
python3 -m compileall render-worker
python3 -m unittest discover render-worker/tests
cd frontend && npm run lint
cd frontend && npm run typecheck
cd frontend && npm run build
docker compose build
docker compose up -d
curl http://localhost:8000/health
```

If a verification script is added, run it and document the result.

Also manually verify the deploy smoke checklist if the local stack can run.

## Completion

Commit and push to `origin/main` with a concise imperative commit message.
