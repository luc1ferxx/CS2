# Render Clip Job V1 Goal

## Context

This project is a website-based CS2 demo coach. The product direction is demo-first:

1. The user uploads a `.dem` file.
2. The app parses it into the existing replay JSON contract.
3. The user can review rounds, tactical map, timeline, and coaching events immediately.
4. When the user wants true CS2 first-person video for a coaching moment, the backend creates a render clip job.
5. A future Windows/Linux GPU worker renders that short clip from the `.dem` on infrastructure we control.

Do not make manual MP4 upload the primary user workflow. It is only a development and QA bridge.

## First Step: Preserve Current Work

Before implementing render clip work:

1. Read `AGENTS.md` and `README.md`.
2. Run `git status --short`.
3. Current uncommitted work may include rules-based coaching v2 and docs updates. Do not revert or overwrite it.
4. Run:
   - `python3 -m compileall backend/app`
   - `PYTHONPATH=backend python3 -m unittest discover backend/tests`
   - `cd frontend && npm run lint`
   - `cd frontend && npm run typecheck`
   - `cd frontend && npm run build`
5. Confirm no `.dem`, `.rar`, `.zip`, `.mp4`, screenshots, Playwright cache, or large local sample files are staged.
6. If verification passes, commit and push current work with:
   - `git add .`
   - `git commit -m "Improve rules coaching and document demo-first render path"`
   - `git push origin main`

## Implementation Goal

Implement Render Clip Job V1 as a backend/frontend boundary only. Do not implement real CS2 rendering yet.

Add a way for the user to request a first-person clip around a coaching event or selected tick. The request should create a render job that is explicit, inspectable, and ready for a future external GPU worker.

## Backend Requirements

Add an endpoint:

- `POST /demos/{demo_id}/render/clip`

The request body should support:

- optional `eventId`
- optional `playerId` or `povSteamId`
- `tickStart`
- `tickEnd`
- `tickRate`
- optional `roundNumber`
- optional `renderPreset`

Validation:

- The demo must exist.
- The replay blob must exist.
- `tickEnd` must be greater than `tickStart`.
- The requested duration must be capped, default max 60 seconds.
- The request must not depend on OpenAI, S3/R2, OBS, ffmpeg, or local CS2 automation.

Create a `demo_jobs` row with job type `render_clip`. Store only compact metadata in Postgres. Do not store large binary payloads in Postgres.

Update the worker to recognize `render_clip`:

- `queued -> rendering -> failed`
- For now, fail with a clear message such as: `Render clip worker is not connected yet. A Windows/Linux GPU worker must process this job.`
- Do not clear or overwrite existing manual uploaded video metadata.
- Do not break the existing `mock_render` boundary.

Add tests for:

- valid render clip request creates a job
- invalid tick range is rejected
- too-long clip is rejected
- missing replay blob returns a clear error
- stub failure does not remove existing `manual_upload` video data

## Frontend Requirements

Add a small "Generate Clip" entry point without redesigning the page:

- Prefer adding it to coaching cards or the coaching panel action area.
- Default the clip range to event tick plus/minus 20 seconds using replay tick rate.
- Include player identity if the event exposes involved player IDs.
- Show queued/rendering/failed state clearly.
- Keep existing click-to-seek behavior.
- Keep first-person fallback shell when `video.url` is null.
- Keep tactical map sync, Dust2 radar, roster display, and manual MP4 calibration intact.

## Documentation Requirements

Update `README.md` with:

- the demo-first product flow
- what `render_clip` does now
- why V1 intentionally fails without a GPU render worker
- future worker contract: input `.dem`, POV player, tick range, output MP4 URL and calibration metadata
- a clear note that users should not need to upload MP4 in the final product

If helpful, update `AGENTS.md` so future Codex sessions keep the same direction.

## Restrictions

Do not:

- connect OpenAI
- add S3/R2
- implement OBS or ffmpeg automation
- automate a local CS2 client
- require control of the user's computer
- render a whole match
- delete or break mock upload
- delete or break real `.dem` parser
- delete or break manual MP4 upload/calibration
- commit local `.dem`, `.rar`, `.mp4`, screenshots, or cache files

## Verification

Run:

- `python3 -m compileall backend/app`
- `PYTHONPATH=backend python3 -m unittest discover backend/tests`
- `cd frontend && npm run lint`
- `cd frontend && npm run typecheck`
- `cd frontend && npm run build`
- `docker compose build`
- `docker compose up -d`
- `curl http://localhost:8000/health`

Manual/browser checks:

- mock upload still completes
- real Dust2 `.dem` upload still parses and creates coaching events
- Generate Clip creates a render clip job and shows current state
- render clip stub failure is understandable
- manual MP4 upload/calibration still works
- desktop and mobile demo detail pages have no console errors

Finish by summarizing changed files, verification results, and known limitations.
