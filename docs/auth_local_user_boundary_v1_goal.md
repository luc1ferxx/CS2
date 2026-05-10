# Auth / Local User Boundary V1 Goal

## Objective

Based on the current `main` branch, implement Auth / Local User Boundary v1.

Do not add OpenAI, do not implement real CS2 rendering, do not change the render-worker architecture, and do not integrate third-party auth. The goal is to add the smallest practical owner boundary around demo, upload, and render job management actions so the app is easier to evolve toward production auth later.

## Start

1. `git pull origin main`
2. Read:
   - `AGENTS.md`
   - `README.md`
3. Inspect:
   - `backend/app/api/demos.py`
   - `backend/app/api/uploads.py`
   - `backend/app/services/demo_service.py`
   - `backend/app/models/demo.py`
   - `backend/app/models/job.py`
   - `backend/app/schemas/demo.py`
   - `backend/tests/`
   - `frontend/app/dashboard/page.tsx`
   - `frontend/app/demos/[demoId]/page.tsx`
   - `frontend/lib/api.ts`
   - `frontend/types/demo.ts`

## Requirements

### 1. Add a dev-only current user boundary

Add a backend helper for resolving the current local development user.

Use a fixed default owner such as `dev-user`.

It may be configurable through an environment variable or request header to make tests straightforward, but do not implement:

- real sessions
- OAuth
- JWT auth
- password login
- account management UI

### 2. Add owner_id to demo data

Add `owner_id` to the demo model and schema surfaces where appropriate.

Requirements:

- New demo creation must write `owner_id`.
- Existing local/dev rows must remain loadable.
- Add a pragmatic backfill/default path for old rows, such as assigning missing owners to the default dev owner.
- Do not break the existing Docker Postgres dev flow.

### 3. Enforce owner access in APIs

Apply owner checks consistently across demo management surfaces.

At minimum:

- `GET /demos` returns only demos owned by the current owner.
- `GET /demos/{demo_id}` does not expose demos owned by another owner.
- Rename cannot modify another owner's demo.
- Archive/unarchive cannot modify another owner's demo.
- `render_clip` creation must verify the target demo belongs to the current owner.
- Render job list/status APIs must not expose another owner's demo jobs.
- Manual video calibration or upload metadata updates must not modify another owner's demo.

Use either `404` or `403` for cross-owner access, but be consistent and cover it in tests.

### 4. Keep the frontend experience mostly unchanged

Do not add a login screen.

Keep the existing dashboard and demo detail workflows intact:

- mock upload
- real `.dem` upload
- search/filter/sort
- rename
- archive
- direct-open demo detail
- round review
- coaching review
- Generate Clip
- render operator panel

If useful, a tiny "Local dev user" indicator is acceptable, but avoid a broad UI redesign.

### 5. Add tests

Add backend tests that cover owner behavior.

Required test coverage:

- Demo list filters by owner.
- Demo detail cannot read another owner's demo.
- Rename cannot modify another owner's demo.
- Archive cannot modify another owner's demo.
- Upload-created demos receive the current owner.
- `render_clip` cannot be created for another owner's demo.
- Render job list/status does not expose another owner's jobs.

Keep frontend tests focused only if helper logic changes. Do not add brittle UI tests unless the implementation makes them necessary.

### 6. Update docs

Update:

- `README.md`
- `AGENTS.md`

Document that this is a dev-only auth boundary, not production authentication.

Also document the intended future replacement path, such as Clerk, Auth0, Supabase Auth, or another production identity provider.

## Preserve

Do not regress:

- mock upload
- real `.dem` upload
- Demo Library search/filter/sort
- rename/archive
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

Also manually verify:

- dashboard loads
- mock upload creates and lists a demo
- real `.dem` upload still parses
- demo detail opens
- rename works
- archive hides the demo by default
- direct-open archived demo behavior remains intentional
- Generate Clip still creates a render job
- render operator panel still refreshes

## Completion

Commit and push to `origin/main` with a concise imperative commit message.

