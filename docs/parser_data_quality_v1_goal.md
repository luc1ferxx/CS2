# Parser Data Quality V1 Goal

## Context

Current `main` already has:

- mock upload and mock replay MVP
- real `.dem` upload and demoparser2 parser spike
- normalized replay JSON contract
- tactical map coverage for Dust2 plus approximate map coverage
- Coaching UI v2 using deterministic rules
- manual MP4 upload/calibration
- `render_clip` job boundary
- Render Worker V1 contract, runner skeleton, fake adapter, and CS2 manual adapter
- stacked Demo Detail layout with 16:9 First-person Replay, tactical map below it, and coaching on the right

Do not change the render worker architecture in this phase. Do not add OpenAI or real CS2 rendering.

## Objective

Improve real `.dem` parser data quality and expose lightweight replay events that the timeline/map/analyzer can use.

Focus on:

- more reliable round metadata
- more structured player state
- structured kill/death data
- bomb events
- utility events
- lightweight frontend visualization of those parser events

This is a parser and replay-data milestone, not a redesign.

## Start

1. `git pull origin main`
2. Read:
   - `AGENTS.md`
   - `README.md`
3. Inspect:
   - `backend/app/parser/demo_parser.py`
   - `backend/app/parser/normalizer.py`
   - `backend/app/analysis/rules.py`
   - `backend/app/analysis/analyzer.py`
   - `backend/app/services/demo_service.py`
   - `backend/app/workers/worker.py`
   - `backend/tests/`
   - `frontend/types/replay.ts`
   - `frontend/components/replay/ReplayViewer.tsx`
   - `frontend/components/replay/Timeline.tsx`
   - `frontend/lib/map-config.ts`
   - `frontend/lib/coaching-review.ts`
   - `frontend/app/demos/[demoId]/page.tsx`

## Backend Requirements

Preserve all existing flows:

- mock upload
- real demo upload
- parser normalization
- coaching rules
- manual MP4 upload/calibration
- `render_clip`
- render-worker/operator panel contracts

Enhance parser output where demoparser2 supports it. Extract and structure as much of this as practical:

- round start tick
- round end tick
- freeze time or live round start tick, if supported
- winner side
- winner reason, if supported
- bomb plant tick/site/player
- bomb defuse tick/player
- bomb explode tick
- kills/deaths/assists, if supported
- attacker/victim Steam ID/name/team/side
- trade relationship, only if reliable
- utility events:
  - smoke
  - flash
  - molotov/incendiary
  - HE grenade
- utility thrower
- utility tick
- utility position
- utility target/landing position, if supported

## Replay Events Contract

Add or extend a backward-compatible normalized replay events field, for example `replay.events`.

Suggested event shape:

```ts
{
  id: string;
  type: "kill" | "death" | "bomb_planted" | "bomb_defused" | "bomb_exploded" | "smoke" | "flash" | "molotov" | "he";
  tick: number;
  roundNumber: number;
  playerId?: string | null;
  playerName?: string | null;
  side?: "T" | "CT" | null;
  x?: number | null;
  y?: number | null;
  label: string;
  metadata?: Record<string, unknown>;
}
```

Implementation notes:

- Keep the current replay JSON contract compatible with old blobs.
- If old replay blobs have no `events`, default to `[]`.
- Mock replay should include minimal compatible events so frontend code does not need many special cases.
- Do not store raw parser dataframes or huge raw JSON in replay blobs.
- Do not fail a whole demo parse just because one event family is missing.
- Parser/normalizer should degrade gracefully and record best-effort metadata only where safe.

## Trade Tagging

If trade tagging can be implemented reliably, add metadata like:

- `isTrade`
- `tradedPlayerId`
- `tradeWindowSeconds`

If it is not reliable from the current parser data, do not fake it. Document it as not enabled or best-effort.

## Analyzer Requirements

Do not rewrite the analyzer rules in this phase.

Keep existing rules v2 behavior stable. The new parser event fields may be added to analyzer inputs later, but this milestone should not change rule semantics unless a tiny compatibility update is required.

## Frontend Requirements

Update `frontend/types/replay.ts` to include the new replay events contract.

Timeline:

- Add parser event markers for:
  - bomb plant
  - bomb defuse
  - bomb explode
  - utility events
  - kills
- Markers should not obscure the slider or existing coaching markers.
- Clicking a parser event marker should seek to the event tick.
- Coaching markers and parser event markers must coexist.
- Use compact type-specific colors or labels.

ReplayViewer/map:

- Keep current bombState rendering.
- Optionally show recent nearby parser events on the map.
- Keep event display restrained so the map does not become noisy.
- Only show current round/current tick nearby events by default if map markers are added.
- Do not break player interpolation, map calibration status, or radar selection.

Demo Detail:

- Preserve current layout:
  - 16:9 First-person Replay on top
  - tactical map below video
  - coaching panel on the right
  - timeline at the bottom
- Do not make a broad redesign.
- Keep Coaching UI v2 filter/search/grouping working.
- Keep event click-to-seek working.
- Keep Generate Clip and RenderOperatorPanel working.

## Tests

Backend tests should cover:

- normalizer tolerates missing bomb fields
- normalizer tolerates missing utility fields
- mock replay has compatible `events`
- real parser sample can output kills/deaths and round metadata where sample files are available
- missing utility data does not fail parse
- old replay blob without `events` is still compatible through the existing service/schema path, if such compatibility layer exists

Frontend/helper tests should cover:

- timeline parser event marker generation
- event type label/color mapping
- coaching markers and parser event markers coexisting
- marker click target tick mapping

Use existing local sample files if present:

- `sample-demos/falcons-vs-furia-m1-dust2.dem`
- `sample-demos/the-mongolz-vs-liquid-ancient.dem`

If samples are missing, use mock/fallback tests. Do not commit demo files.

## Documentation

Update `README.md` with:

- Parser Data Quality v1 scope
- replay events contract
- supported event types
- which fields are reliable
- which fields are best-effort
- current demoparser2 limitations
- note that utility/bomb/trade data must be tolerant of parser gaps

Update `AGENTS.md` if helpful:

- Keep replay contract backward compatible.
- Do not store large parser outputs in Postgres or replay blobs.
- Do not let one missing event family fail the entire demo parse.

## Restrictions

Do not:

- connect OpenAI
- implement real CS2 rendering
- call OBS
- call ffmpeg
- connect S3/R2
- change render-worker architecture
- submit `.dem`, `.mp4`, `.rar`, screenshots, caches, or large files
- break mock upload
- break real `.dem` parser
- break manual MP4 upload/calibration
- break `render_clip`
- break render-worker/operator panel
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
- any new timeline/replay event helper tests
- `cd frontend && npm run lint`
- `cd frontend && npm run typecheck`
- `cd frontend && npm run build`
- `docker compose build`
- `docker compose up -d`
- `curl http://localhost:8000/health`

Manual/browser verification:

- mock upload still works and returns compatible replay events
- real Dust2 `.dem` parse still works
- real Dust2 replay includes kills/deaths/round/event metadata where available
- Ancient parse still works if local sample exists
- Demo Detail desktop has no console errors
- Demo Detail mobile has no console errors
- Timeline coaching markers still click-to-seek
- Timeline parser event markers click-to-seek
- Tactical map still uses correct map resource and does not become visually noisy

## Commit

If all verification passes:

```bash
git add .
git commit -m "Improve parser replay event data"
git push origin main
```

Final response should include:

- commit hash
- changed files summary
- verification summary
- replay event types supported
- reliable fields
- best-effort fields
- known limitations
