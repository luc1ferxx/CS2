# Cloud Preview Deploy V1 Goal

## Objective

Based on the current `main` branch, implement Cloud Preview Deploy v1.

Do not add OpenAI, do not implement real CS2 rendering, do not replace the dev-only owner boundary with production auth, and do not commit secrets. The goal is to make the app ready for a small public or private preview deployment with clear configuration, deploy docs, and smoke checks while preserving local Docker development.

This step is about deployment readiness and preview wiring. It does not need to actually deploy to a cloud provider unless credentials and a target platform are already available in the environment.

## Start

1. `git pull origin main`
2. Read:
   - `AGENTS.md`
   - `README.md`
   - `docs/deployment_readiness_v1.md`
   - `docs/storage_boundary_v1_goal.md`
   - `docker-compose.yml`
3. Inspect:
   - `backend/app/main.py`
   - `backend/app/core/config.py` if present
   - `backend/app/services/storage.py`
   - `backend/app/api/`
   - `backend/app/workers/worker.py`
   - `frontend/lib/api.ts`
   - `frontend/next.config.*`
   - `frontend/package.json`
   - `scripts/verify.sh`
   - Dockerfiles and compose files if present

## Requirements

### 1. Choose and document the preview deployment shape

Pick one pragmatic primary preview path for this repo.

Prefer a Docker Compose based preview target unless the existing codebase already clearly favors another path. Examples:

- single VPS / VM running Docker Compose
- Render services
- Fly.io machines
- Railway services

Do not add Terraform, Kubernetes, or a broad multi-provider abstraction.

Add a focused doc such as `docs/cloud_preview_deploy_v1.md` that explains:

- selected preview shape
- services involved: frontend, API, worker, Postgres, Redis
- expected public URLs
- storage/media assumptions
- what remains local/dev-only
- rollback/redeploy basics

### 2. Make environment configuration explicit

Add or refine environment examples without committing secrets.

Create or update files such as:

- `.env.example`
- `backend/.env.example`
- `frontend/.env.example`
- deployment-specific example env blocks in docs

Cover at minimum:

- `DATABASE_URL`
- `REDIS_URL`
- backend public URL
- frontend public API URL
- CORS allowed origins
- local storage root / media URL base
- `DEV_USER_ID`
- fake/manual render settings
- render-worker callback/API settings

Keep local defaults easy to run.

### 3. Validate public URL and CORS assumptions

Review frontend/backend API URL handling.

Requirements:

- frontend can point to a configurable public API URL
- backend CORS allows a configured frontend origin
- media/video URLs can work when frontend and backend are on different hosts
- local `localhost` behavior remains unchanged

Avoid hardcoded `localhost` values in production-facing paths.

### 4. Tighten Docker preview startup

Review Docker Compose and service startup behavior.

Make small, safe changes only if needed:

- health checks remain meaningful
- service dependencies are clear
- worker starts after API dependencies are available
- storage volumes are explicit
- logs are usable enough for preview debugging

Do not introduce secrets into Compose files. Use env vars and examples.

### 5. Add deploy smoke checklist or script

Add a simple deploy smoke process.

It can be a script, Makefile target, or documented commands. Keep it practical.

It should verify:

- API `/health`
- frontend dashboard loads
- mock upload works
- real `.dem` upload works if a sample is available
- demo detail opens
- round review sync works
- Generate Clip creates a render job/status
- media URLs return expected responses

This can be documented as manual browser smoke if full automation would be brittle.

### 6. Clarify limitations for preview users

Update docs to state clearly:

- dev owner boundary is not production auth
- preview should not be used for untrusted public multi-user traffic
- fake/manual render adapters are not real GPU rendering
- local filesystem storage is still an MVP preview path unless replaced later
- uploaded `.dem` files are untrusted inputs
- no OpenAI/LLM workflow is part of the product yet

## Preserve

Do not regress:

- local `docker compose up`
- `./scripts/verify.sh`
- mock upload
- real `.dem` upload
- Demo Library search/filter/sort
- rename/archive
- dev owner boundary
- storage boundary compatibility
- round review
- parser events
- rules analyzer v3
- coaching UI
- timeline parser/coaching markers
- tactical map sync
- Generate Clip / `render_clip`
- RenderOperatorPanel
- fake/manual render-worker callback flows

## Tests

Add focused tests only where code behavior changes.

Likely test areas:

- CORS/env config parsing
- public API/media URL generation
- health response remains stable
- storage/media URL behavior when base URL env vars are set

Do not add brittle browser automation unless existing local tooling already makes it straightforward.

## Verification

Run the relevant checks before committing:

```bash
./scripts/verify.sh
docker compose build
docker compose up -d
curl http://localhost:8000/health
```

Also manually verify locally:

- dashboard loads on `http://localhost:3000`
- mock upload creates and lists a demo
- real `.dem` upload parses if a sample is available
- demo detail opens
- replay/map/timeline sync works
- Generate Clip creates a render job
- render operator panel refreshes
- media URL smoke returns expected status

If preview deployment is actually performed, document:

- deployment target
- public URLs
- env vars used, excluding secrets
- smoke test result
- known limitations

## Completion

Commit and push to `origin/main` with a concise imperative commit message.
