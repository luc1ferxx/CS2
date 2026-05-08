# Rules Analyzer V3 Goal

## Objective

Implement Rules Analyzer v3 on top of the current `main` branch.

Do not connect OpenAI. Do not implement real CS2 rendering. Do not change the render-worker architecture.

The goal is to use Parser Data Quality v1 `replay.events` so deterministic coaching rules can reason about structured kill, bomb, and utility events.

## Start

1. `git pull origin main`
2. Read:
   - `AGENTS.md`
   - `README.md`
3. Inspect:
   - `backend/app/analysis/rules.py`
   - `backend/app/analysis/analyzer.py`
   - `backend/app/parser/normalizer.py`
   - `backend/app/services/mock_replay_service.py`
   - `backend/tests/test_rules_analyzer.py`
   - `frontend/types/coaching.ts`
   - `frontend/lib/coaching-review.ts`
   - `frontend/components/coaching/CoachingPanel.tsx`
   - `frontend/components/coaching/CoachingEventCard.tsx`
   - `frontend/types/replay.ts`
   - `frontend/lib/replay-events.ts`

## Requirements

Preserve:

- existing v2 rules
- current coaching UI behavior
- mock upload
- real `.dem` parser
- parser event markers
- Generate Clip
- RenderOperatorPanel
- stacked Demo Detail layout
- 16:9 First-person Replay

Use `replay.events` to enhance analyzer logic.

Add 2-4 deterministic v3 rules. Prefer rules that are testable, explainable, and tolerant of missing parser data:

- `weak_utility_before_execute`
- `late_post_plant_utility`
- `post_plant_spacing_with_bomb_event`
- `retake_desync_with_bomb_event`
- `untraded_death_with_kill_event`

Do not implement every possible rule if the data is not reliable enough. Choose the rules that can be implemented cleanly with the existing replay frames/events.

Every new or enhanced rule should output clear metadata:

- `ruleId`
- `involvedPlayerIds`
- `evidenceTicks`
- `relatedEventIds`
- relevant `distance`
- relevant `windowSeconds`
- utility/bomb/kill details when useful

Rules must be deterministic and explainable.

Do not let missing bomb, utility, or kill events crash analysis. Missing event families should degrade gracefully.

Mock replay should continue to generate stable coaching events.

## Backend Notes

Keep the analyzer API and persisted coaching event shape compatible with existing frontend code.

If adding helper functions, keep them close to `backend/app/analysis/rules.py` unless the file becomes clearly too large.

Do not change parser output semantics unless a tiny compatibility fix is required.

Do not store raw parser dataframes or huge raw structures in coaching metadata.

If a rule depends on best-effort parser events, make that clear in metadata and docs.

## Frontend Requirements

Make only small compatibility improvements.

Coaching cards should be able to display new v3 metadata such as:

- `relatedEventIds`
- `evidenceTicks`
- utility event labels/types
- bomb event labels/types
- distance/window metadata

Do not redesign the Demo Detail page.

Do not break:

- coaching filters/search/grouping
- event click-to-seek
- timeline coaching markers
- parser event markers
- Generate Clip for event
- RenderOperatorPanel refresh/status
- tactical map sync
- current stacked layout
- 16:9 First-person Replay

If metadata summary helpers already exist, extend those rather than duplicating formatting in components.

## Tests

Add or update backend tests for:

- analyzer does not crash when `replay.events` is missing
- analyzer does not crash when `replay.events` is empty
- bomb event present: post-plant or retake rule uses bomb tick/event id
- utility events present: weak utility or late utility rule can trigger
- utility events missing: utility-dependent rules do not false-positive
- v2 rules still pass
- metadata includes `relatedEventIds`
- metadata includes `evidenceTicks`
- mock replay analysis remains stable

Add or update frontend/helper tests if there is an existing pattern:

- coaching metadata summary displays new fields
- unknown metadata still does not crash
- event metadata with `relatedEventIds` is readable

Do not add a heavy frontend testing framework if the repo does not already use one.

## Documentation

Update `README.md` with:

- Rules Analyzer v3 scope
- new or enhanced rules
- which rules depend on parser events
- which fields are reliable
- which fields are best-effort
- explicit note that this is deterministic rules-based coaching, not OpenAI/AI coaching

Update `AGENTS.md` if helpful:

- analyzer rules must tolerate missing parser events
- coaching metadata must remain explainable
- do not introduce OpenAI into analyzer rules
- do not make one missing parser event family fail a whole analysis run

## Restrictions

Do not:

- connect OpenAI
- implement real CS2 rendering
- call OBS
- call ffmpeg
- connect S3/R2
- change render-worker architecture
- commit `.dem`, `.mp4`, `.rar`, screenshots, caches, or large local files
- break mock upload
- break real parser
- break parser event markers
- break Coaching UI v2
- break manual MP4 upload/calibration
- break `render_clip`
- break render-worker/operator panel
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
- any new frontend helper tests
- `cd frontend && npm run lint`
- `cd frontend && npm run typecheck`
- `cd frontend && npm run build`
- `docker compose build`
- `docker compose up -d`
- `curl http://localhost:8000/health`

Manual/browser verification:

- mock upload still completes
- real Dust2 parse still completes
- coaching events still display
- v3 metadata is readable in coaching cards
- event click-to-seek works
- parser event marker click-to-seek works
- Generate Clip still creates render clip job
- RenderOperatorPanel still refreshes state
- Demo Detail desktop has no console errors
- Demo Detail mobile has no console errors
- stacked layout and 16:9 First-person Replay are preserved

## Commit

If all verification passes:

```bash
git add .
git commit -m "Improve rules analyzer with replay events"
git push origin main
```

Final response should include:

- commit hash
- changed files summary
- new/enhanced rules list
- verification summary
- reliable fields
- best-effort fields
- known limitations
