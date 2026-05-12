# Product Polish / Demo Flow V1 Goal

## Objective

Based on the current `main` branch, implement Product Polish / Demo Flow v1.

Do not add OpenAI, do not implement real CS2 rendering, do not replace the dev-only owner boundary with production auth, and do not turn the dashboard into a marketing landing page. The goal is to make the existing demo upload and review flow feel clear, professional, and easy to understand for a first-time preview user.

## Start

1. `git pull origin main`
2. Read:
   - `AGENTS.md`
   - `README.md`
   - `docs/deployment_readiness_v1.md`
   - `docs/cloud_preview_deploy_v1.md`
   - `docs/sample_demo_fixture_v1.md`
3. Inspect:
   - `frontend/app/dashboard/page.tsx`
   - `frontend/app/demos/[demoId]/page.tsx`
   - `frontend/app/globals.css`
   - `frontend/lib/api.ts`
   - `frontend/types/demo.ts`
   - `frontend/types/replay.ts`
   - `frontend/lib/map-config.ts`
   - `frontend/components/replay/ReplayViewer.tsx`
   - `frontend/components/replay/RenderOperatorPanel.tsx`
   - `frontend/components/coaching/CoachingPanel.tsx`
   - `backend/app/api/uploads.py`
   - `backend/app/api/demos.py`
   - `backend/app/schemas/demo.py`
   - `backend/tests/`

## Requirements

### 1. Improve Dashboard empty and loading states

Keep Dashboard as a working Demo Library, not a landing page.

Improve:

- empty state when no demos exist
- loading state while demos are fetched
- failed fetch state
- archived/filter empty state
- search/filter no-results state

Make actions clear:

- create mock demo
- upload real `.dem`
- refresh library

Avoid oversized hero layouts, decorative marketing sections, or vague product copy.

### 2. Clarify upload actions and status

Make the difference between upload paths clear:

- mock demo generation for local preview
- real `.dem` upload for parser flow
- optional sample demo smoke is a developer/deployment tool, not a normal user upload path

After upload:

- show queued/parsing/analyzing/completed/failed state clearly
- surface concise parse failure reason when available
- make it easy to open the newly created demo
- keep polling behavior stable and bounded

### 3. Improve Demo Detail summary header

Add or refine a compact summary near the top of Demo Detail.

Include useful fields when available:

- demo name / file name
- map display name
- calibration/fallback status
- rounds count
- coaching events count
- parser status / failure category
- render/video availability summary

Keep the current layout:

- 16:9 first-person replay
- controls between video and tactical map
- tactical map
- round review
- coaching panel
- timeline markers
- render operator panel

Do not perform a broad page redesign.

### 4. Improve failure and diagnostics copy

Use diagnostics data where it already exists, but keep UI compact.

Improve text for:

- invalid/corrupt demo
- parser unsupported/partial parse
- worker/Redis not connected
- render clip GPU worker not connected
- media URL missing/unavailable

Avoid exposing:

- stack traces
- local absolute paths
- secrets
- raw parser data

### 5. Polish responsive layout

Verify desktop and mobile.

Fix only concrete UI issues:

- overflowing text
- clipped buttons
- controls too far from replay
- panels with awkward empty space
- status text that wraps poorly

Keep the existing dense, utilitarian review-tool style.

### 6. Add small frontend helper tests where useful

If status label, summary metadata, or filter helper logic is extracted, add focused tests.

Do not add brittle browser screenshots unless current tooling already supports it cleanly.

### 7. Update docs

Update:

- `README.md`
- `AGENTS.md`
- relevant preview/deployment docs if user-facing flow changes

Document:

- intended first-run preview flow
- mock vs real upload difference
- how parse/render failures appear
- known limits: dev owner boundary, fake/manual render, no OpenAI

## Preserve

Do not regress:

- `./scripts/verify.sh`
- cloud preview smoke
- sample demo smoke
- mock upload
- real `.dem` upload
- parser reliability v2 failure handling
- diagnostics endpoint
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

Add focused frontend/backend tests if behavior changes.

Likely areas:

- status label helpers
- demo summary helper
- upload polling helper
- parse failure display mapping

Keep tests pragmatic and stable.

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

Manual browser smoke:

- dashboard empty/loading/normal states
- mock upload creates and opens a demo
- real/sample `.dem` upload parses when configured
- corrupt `.dem` failure text is useful
- search/filter/sort/rename/archive still work
- demo detail summary is clear
- replay/map/timeline sync still works
- coaching click-to-seek still works
- Generate Clip still creates a render job
- render operator panel state is readable
- desktop/mobile views have no console errors

## Completion

Commit and push to `origin/main` with a concise imperative commit message.

