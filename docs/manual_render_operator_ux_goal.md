# Manual Render Operator UX Goal

## Context

Current `main` already has:

- real `.dem` upload and parser
- tactical replay and coaching events
- manual MP4 upload/calibration
- `render_clip` job boundary
- Render Worker V1 contract and callback
- render-worker runner skeleton
- `FakeVideoAdapter`
- `CS2ManualAdapter` for `prepare-job` and manual completion

We are still on a MacBook development machine, so do not implement real CS2 rendering. The goal is to make the manual render/operator flow visible and easier to operate from the app.

## Objective

Improve the operator UX around `render_clip` jobs without changing the core architecture.

After a user clicks Generate Clip, the app should make it clear whether the clip is:

- queued
- waiting for render worker
- prepared for manual CS2 recording
- waiting for MP4 output
- completed and playable
- failed with a clear error

The UI should help a developer/operator understand what to do next, but it must not require controlling the user's computer.

## Start

1. `git pull origin main`
2. Read:
   - `AGENTS.md`
   - `README.md`
   - `render-worker/README.md`
   - `docs/render_clip_v1_goal.md` if present
3. Inspect:
   - `backend/app/api/demos.py`
   - `backend/app/services/demo_service.py`
   - `backend/app/workers/worker.py`
   - `backend/app/schemas/demo.py`
   - `frontend/app/demos/[demoId]/page.tsx`
   - `frontend/components/replay/FirstPersonReplay.tsx`
   - `frontend/components/replay/VideoSetupPanel.tsx`
   - `frontend/types/replay.ts`
   - `render-worker/runner.py`
   - `render-worker/adapters/cs2_manual.py`

## Backend Requirements

Add or refine read-only/dev-safe endpoints for render operator state.

Suggested endpoints, adjust to existing patterns:

- `GET /demos/{demo_id}/render/jobs`
  - returns recent render jobs for the demo
  - includes job id, type, status, created/updated time, error message, requested tick range, POV/player if available, source
- `GET /render-worker/jobs/{job_id}/manifest`
  - token-gated if it exposes worker-only details
  - returns the manifest used by the worker
- Optional: `GET /render-worker/jobs/{job_id}/operator-state`
  - returns whether a manual prepared workspace is expected, what output filename/path is expected, and whether callback is possible
  - keep this dev/operator only if needed

Do not store large files in Postgres.

If the backend does not know the local render-worker workspace path, do not fake certainty. Return only what the API can know. Put local workspace details in render-worker generated files/docs.

Preserve:

- mock upload
- real `.dem` parser
- manual MP4 upload/calibration
- `render_clip` creation
- render worker callback contract
- token gate

## Render Worker Requirements

Improve `CS2ManualAdapter` operator artifacts if needed:

- `instructions.md` should be concise and actionable
- `manifest.json` should contain enough information to identify demo, job id, POV, tick range, tick rate, round
- `expected_output.json` should include expected MP4 filename and callback metadata
- If useful, add a small `status.json` in the prepared workspace with:
  - `preparedAt`
  - `expectedVideoPath`
  - `state: waiting_for_manual_recording`
  - `nextAction`

Do not add real CS2 automation.

## Frontend Requirements

Improve Demo Detail render UX with a small operator/status panel.

Do not redesign the whole page.

The panel should show:

- latest `render_clip` job status
- requested tick range and approximate seconds
- selected event/player if available
- whether current video is mock shell, manual upload, rendered, queued, rendering, failed
- clear failed message
- if completed with `video.url`, indicate playable rendered clip
- if waiting/manual, explain that a render worker/operator must prepare and complete the job

Actions:

- Keep existing Generate Clip button
- Allow refresh/poll of render job status
- Do not break coaching card click-to-seek
- Do not break first-person fallback shell
- Do not break tactical map sync
- Do not break manual MP4 calibration

Tone/UI:

- This is an internal dev/operator panel, not a marketing explanation.
- Keep it compact and utilitarian.
- Avoid large redesigns and decorative UI.

## Documentation Requirements

Update:

- `README.md`
- `render-worker/README.md`
- `AGENTS.md` if helpful

Document:

- how Generate Clip relates to `render_clip` jobs
- how to inspect job status
- how to run `CS2ManualAdapter prepare-job`
- where operator artifacts are generated
- how to complete a prepared job with an MP4
- that MacBook development can verify the full contract using fake/manual MP4, but cannot perform real CS2 rendering

## Tests

Add or adjust backend tests for:

- listing render jobs by demo
- manifest endpoint if added
- failed/manual/completed render job state serialization
- preserving manual upload metadata

Add or adjust frontend tests only if the repo already has a pattern for it. Otherwise rely on lint, typecheck, and browser checks.

Add or adjust render-worker tests for:

- instructions/status artifact generation
- prepared workspace state
- expected output metadata

## Restrictions

Do not:

- implement real CS2 automation
- launch Steam
- launch CS2
- call OBS
- call ffmpeg
- connect OpenAI
- connect S3/R2
- require user computer permissions
- submit `.dem`, `.mp4`, `.rar`, screenshots, caches, or large files
- break mock upload, real parser, manual MP4 upload, `render_clip`, render-worker fake flow, or `CS2ManualAdapter`

## Verification

Run:

- `python3 -m compileall backend/app`
- `PYTHONPATH=backend python3 -m unittest discover backend/tests`
- `python3 -m compileall render-worker`
- render-worker tests
- `cd frontend && npm run lint`
- `cd frontend && npm run typecheck`
- `cd frontend && npm run build`
- `docker compose build`
- `docker compose up -d`
- `curl http://localhost:8000/health`

Manual/browser verification:

- mock upload still works
- real Dust2 `.dem` parse still works
- Generate Clip creates `render_clip` job
- render job status appears in demo detail
- `CS2ManualAdapter prepare-job` generates manifest/instructions/expected_output/status artifacts
- `complete-prepared-job` with an existing MP4 updates rendered video metadata
- failed callback shows clear error and does not break manual upload
- desktop and mobile demo detail pages have no console errors

## Commit

If all verification passes:

```bash
git add .
git commit -m "Improve manual render operator UX"
git push origin main
```

Final response should include:

- commit hash
- changed files summary
- verification summary
- known limitations
