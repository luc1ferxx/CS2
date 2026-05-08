# Demo Library V1 Goal

## Objective

Implement Demo Library / Project Management v1 on top of the current `main` branch.

Do not connect OpenAI. Do not implement real CS2 rendering. Do not change the render-worker architecture.

The goal is to upgrade Dashboard from a development test page into a usable demo library where users can manage multiple demos.

## Start

1. `git pull origin main`
2. Read:
   - `AGENTS.md`
   - `README.md`
3. Inspect:
   - `frontend/app/dashboard/page.tsx`
   - `frontend/app/demos/[demoId]/page.tsx`
   - `frontend/lib/api.ts`
   - `frontend/types/demo.ts`
   - `backend/app/api/demos.py`
   - `backend/app/api/uploads.py`
   - `backend/app/services/demo_service.py`
   - `backend/app/models/demo.py`
   - `backend/app/schemas/demo.py`
   - `backend/tests/`
   - `docker-compose.yml`

## Preserve

Do not break:

- mock upload
- real `.dem` upload
- parser events
- rules analyzer v3
- round review workflow
- stacked Demo Detail layout
- 16:9 First-person Replay
- tactical map
- timeline markers
- Generate Clip / `render_clip`
- render-worker/operator panel
- manual MP4 calibration

## UX Goals

Dashboard should become Demo Library, not a simple test table.

Users should be able to:

- view all demos
- search demo name, original filename, and map
- filter by status, map, and date if practical
- sort by recently uploaded, name, map, or status
- clearly see parse/coaching/render status
- see failed parse/upload reasons
- open demo detail
- rename demos
- archive/hide demos or delete demos safely
- upload mock and real demos with clear status feedback

## Backend Requirements

Prefer small, pragmatic API changes.

Add or refine endpoints as needed:

- `GET /demos`
  - support query params if useful:
    - `search`
    - `status`
    - `map`
    - `sort`
    - `order`
    - `includeArchived`
- `PATCH /demos/{demo_id}`
  - update demo name
  - optionally update an archived flag if supported
- `DELETE /demos/{demo_id}` or `POST /demos/{demo_id}/archive`
  - prefer soft archive over hard delete unless hard delete is already safe
  - do not delete local large files unless clearly implemented and tested

Keep response schemas typed and backward compatible.

If adding archive support:

- add a model field if needed
- default `archived=false`
- list excludes archived by default
- `includeArchived=true` can show archived
- demo detail should still work if directly opened

Do not put large files in Postgres.

Backend tests should cover:

- list demos default behavior
- search/filter/sort
- rename demo
- archive/delete demo
- archived demos hidden by default
- `includeArchived=true` shows archived demos
- existing mock upload/list behavior still works

## Frontend Requirements

Refactor Dashboard into a useful library view.

Required UI:

1. Header/toolbar
   - Mock Upload button
   - Real Demo Upload button
   - search input
   - status filter
   - map filter
   - sort selector

2. Demo list/table/cards
   - demo name
   - original filename
   - map
   - status
   - round count
   - coaching count
   - created/updated time
   - parse error if failed
   - render/video status if available from existing data or API
   - action: Open
   - action: Rename
   - action: Archive/Delete

3. Rename flow
   - inline edit or compact modal
   - optimistic or refresh-based update is fine
   - show an error state if rename fails

4. Archive/Delete flow
   - ask for confirmation before destructive or hidden action
   - prefer the label Archive if soft delete is implemented
   - refresh list after action

5. Empty states
   - no demos
   - no search results
   - failed upload

6. Upload status
   - when the user uploads mock or real demo, show queued/parsing/analyzing/completed clearly
   - keep polling behavior if present
   - do not block real uploads with a broad redesign

7. Responsive design
   - desktop: dense table or list
   - mobile: stacked rows/cards
   - no text overlap
   - controls wrap cleanly

Keep the style utilitarian and review-tool focused. Do not make a marketing landing page.

## Tests

Frontend:

- If there is an existing lightweight helper pattern, add helper tests for filtering/sorting demo list.
- Otherwise rely on lint, typecheck, build, and browser verification.

Backend:

- Add or update unit tests for list query behavior, rename, archive/delete.

Existing tests must keep passing:

- backend tests
- render-worker tests
- frontend helper tests

## Documentation

Update `README.md` with:

- Demo Library v1 scope
- search/filter/sort behavior
- rename/archive behavior
- upload flow
- current limitations

Update `AGENTS.md` if helpful:

- Dashboard is a working review library, not a marketing page.
- Prefer soft archive over destructive delete.
- Keep large upload files out of Postgres.

## Restrictions

Do not:

- connect OpenAI
- implement real CS2 rendering
- call OBS
- call ffmpeg
- connect S3/R2
- change render-worker architecture
- break parser events
- break rules analyzer v3
- break round review workflow
- break current stacked layout
- break 16:9 First-person Replay
- commit `.dem`, `.mp4`, `.rar`, screenshots, caches, or large local files
- modify GitHub remote

## Verification

Run:

- `python3 -m compileall backend/app`
- `PYTHONPATH=backend python3 -m unittest discover backend/tests`
- `python3 -m compileall render-worker`
- `python3 -m unittest discover render-worker/tests`
- `node frontend/lib/coaching-review.test.mjs`
- `node frontend/lib/replay-events.test.mjs`
- `node frontend/lib/round-review.test.mjs`
- any new frontend helper tests
- `cd frontend && npm run lint`
- `cd frontend && npm run typecheck`
- `cd frontend && npm run build`
- `docker compose build`
- `docker compose up -d`
- `curl http://localhost:8000/health`

Manual/browser verification:

- dashboard loads
- mock upload works
- real Dust2 upload works if sample exists
- search works
- status/map filters work
- sort works
- rename works
- archive/delete works with confirmation
- archived demo is hidden by default
- open demo detail still works
- round review/coaching/parser markers still work on detail
- desktop/mobile dashboard has no console errors
- desktop/mobile demo detail has no console errors

## Commit

If all verification passes:

```bash
git add .
git commit -m "Add demo library management"
git push origin main
```

Final response should include:

- commit hash
- changed files summary
- verification summary
- known limitations
