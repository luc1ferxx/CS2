# Round Review UX V1 Goal

## Objective

Implement Round Review UX v1 on top of the current `main` branch.

Do not connect OpenAI. Do not implement real CS2 rendering. Do not change the render-worker architecture.

The goal is to make Demo Detail more useful for round-by-round review, instead of relying only on a long timeline and coaching list.

## Start

1. `git pull origin main`
2. Read:
   - `AGENTS.md`
   - `README.md`
3. Inspect:
   - `frontend/app/demos/[demoId]/page.tsx`
   - `frontend/components/replay/Timeline.tsx`
   - `frontend/components/replay/ReplayViewer.tsx`
   - `frontend/components/coaching/CoachingPanel.tsx`
   - `frontend/components/coaching/CoachingEventCard.tsx`
   - `frontend/lib/coaching-review.ts`
   - `frontend/lib/replay-events.ts`
   - `frontend/types/replay.ts`
   - `frontend/types/coaching.ts`
   - `backend/app/parser/normalizer.py`
   - `backend/app/analysis/rules.py`
   - `backend/app/schemas/demo.py`

## Preserve

Keep these existing surfaces working:

- current stacked Demo Detail layout
- 16:9 First-person Replay
- tactical map below video
- coaching panel on the right
- timeline at the bottom
- parser event markers
- coaching markers
- Generate Clip
- RenderOperatorPanel
- manual MP4 calibration
- mock upload
- real parser
- rules analyzer v3

## UX Goals

Add a round-centric review layer without a broad redesign.

The user should be able to:

- see all rounds in a compact list/sidebar/strip
- understand each round's outcome and important event counts
- click a round to jump to that round
- quickly jump to round start, freeze end/live start, first kill, and bomb plant
- see the current round summary
- keep video, map, timeline, and coaching synchronized

## Frontend Requirements

Add a Round Review component or section.

Suggested files:

- `frontend/components/replay/RoundReviewPanel.tsx`
- `frontend/lib/round-review.ts`
- `frontend/lib/round-review.test.mjs`

Round list item should show:

- round number
- winner side
- tick range or duration
- kill count
- bomb event count
- utility event count
- coaching event count
- selected/current state

Round summary/header should show:

- selected round number
- winner side
- start tick
- freeze end tick if available
- end tick
- first kill tick/player if available
- bomb plant tick/site if available
- counts for kills, utility, and coaching events

Add quick actions:

- Jump round start
- Jump freeze end / live start if available
- Jump first kill if available
- Jump bomb plant if available

Integrate with existing state:

- changing selected round should update `currentTick`
- `currentTick` should still update `selectedRound` when the user seeks manually
- timeline round selector should remain usable
- `RoundReviewPanel` should not fight `Timeline`
- coaching click-to-seek should still update `selectedRound`
- parser marker click-to-seek should still update `selectedRound`

Coaching behavior:

- Default coaching panel can continue showing all events, but selected/current round should be clearly highlighted.
- If implementing a round filter toggle, keep it simple:
  - All events
  - Current round
- Do not remove existing severity/rule/search filters.

Timeline:

- Keep current timeline UI.
- Ensure markers remain scoped to the selected round.
- If adding current-round event counts, keep it compact.
- Do not let parser markers and coaching markers overlap badly.

Styling:

- Use a utilitarian review-tool style.
- Keep it dense but readable.
- Do not make a marketing layout.
- Do not do a large redesign.
- Desktop and mobile must both work.
- Text and buttons must not overlap.

## Backend Requirements

Prefer no backend changes.

Only add backend/schema fields if the frontend cannot derive round summaries from existing replay rounds, replay events, and coaching events.

Do not change parser semantics or analyzer semantics for this milestone.

## Tests

Add frontend helper tests for round review calculations:

- round summary counts kills, bomb events, utility events, and coaching events correctly
- first kill detection
- bomb plant detection
- freeze end fallback if missing
- unknown or missing `replay.events` does not crash
- current round detection by tick
- selected round event counts do not include other rounds

Keep existing tests passing:

- `node frontend/lib/coaching-review.test.mjs`
- `node frontend/lib/replay-events.test.mjs`

Backend tests should not need changes unless a backend/schema change is made.

## Documentation

Update `README.md` with:

- Round Review UX v1 scope
- round list/summary behavior
- quick jumps
- current limitations

Update `AGENTS.md` if helpful:

- Demo Detail should remain review-tool focused.
- Round workflows should keep video, map, timeline, and coaching synchronized.

## Restrictions

Do not:

- connect OpenAI
- implement real CS2 rendering
- call OBS
- call ffmpeg
- connect S3/R2
- change render-worker architecture
- change parser/analyzer semantics unless absolutely needed
- commit `.dem`, `.mp4`, `.rar`, screenshots, caches, or large files
- break mock upload
- break real parser
- break parser event markers
- break rules analyzer v3
- break Coaching UI v2
- break current stacked layout
- break 16:9 First-person Replay
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
- `cd frontend && npm run lint`
- `cd frontend && npm run typecheck`
- `cd frontend && npm run build`
- `docker compose build`
- `docker compose up -d`
- `curl http://localhost:8000/health`

Manual/browser verification:

- mock upload works
- real Dust2 parse works
- Round Review list renders
- selecting round jumps video/map/timeline
- quick jumps work
- coaching click-to-seek still works
- parser marker click-to-seek still works
- Generate Clip still works
- RenderOperatorPanel still works
- desktop/mobile no console errors
- stacked layout and 16:9 replay are preserved

## Commit

If all verification passes:

```bash
git add .
git commit -m "Add round review workflow"
git push origin main
```

Final response should include:

- commit hash
- changed files summary
- verification summary
- known limitations
