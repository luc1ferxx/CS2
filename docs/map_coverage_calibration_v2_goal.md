# Map Coverage + Calibration V2 Goal

## Objective

Based on the current `main` branch, implement Map Coverage + Calibration v2.

Do not add OpenAI, do not implement real CS2 rendering, do not change the render-worker architecture, and do not hardcode per-map coordinate math inside replay UI components or parser call sites. The goal is to make tactical map rendering credible for common CS2 maps by centralizing map configuration, adding calibration metadata, and making fallback states explicit.

## Start

1. `git pull origin main`
2. Read:
   - `AGENTS.md`
   - `README.md`
   - `docs/deployment_readiness_v1.md`
   - `docs/parser_reliability_v2_goal.md`
   - `docs/sample_demo_fixture_v1.md`
   - `frontend/public/maps/ATTRIBUTION.md`
3. Inspect:
   - `backend/app/parser/map_config.py`
   - `backend/app/parser/normalizer.py`
   - `backend/app/parser/demo_parser.py`
   - `backend/tests/`
   - `frontend/lib/map-config.ts`
   - `frontend/components/replay/ReplayViewer.tsx`
   - `frontend/components/replay/Timeline.tsx`
   - `frontend/types/replay.ts`
   - `frontend/lib/replay-events.ts`
   - `scripts/cloud_preview_smoke.py`
   - `.gitignore`

## Requirements

### 1. Expand supported map coverage

Support at least these maps:

- `de_dust2`
- `de_mirage`
- `de_inferno`
- `de_ancient`
- `de_nuke`
- `de_anubis`

For each map, define:

- map name
- display name
- radar image path
- coordinate transform metadata
- calibration status
- optional notes/source

Keep existing Dust2 behavior covered by regression tests.

### 2. Centralize map config

All tactical map metadata must flow through centralized config:

- backend: `backend/app/parser/map_config.py`
- frontend: `frontend/lib/map-config.ts`

Do not add map-specific hardcoding inside:

- `ReplayViewer.tsx`
- normalizer call sites
- parser event helpers
- page components

If backend and frontend configs are duplicated, keep the shape intentionally aligned and document how to update both.

### 3. Add or verify radar assets

Add CS2 radar/overview assets for supported maps if they are missing.

Requirements:

- use CS2-era radar images, not old CS:GO assets when avoidable
- keep files reasonably small for git
- place under `frontend/public/maps/`
- update `frontend/public/maps/ATTRIBUTION.md`
- do not commit huge source assets or unrelated media

If exact assets are unavailable, implement the config and fallback behavior, then document the missing asset clearly rather than silently pretending the map is calibrated.

### 4. Make fallback map state explicit

Unknown or uncalibrated maps must not silently use Dust2 transforms.

Requirements:

- backend replay contract should include enough metadata for calibrated vs fallback display
- frontend should show a compact fallback/calibration status near the tactical map
- fallback still renders safely
- player markers should not explode layout when coordinates are unavailable or invalid
- sample smoke should print map calibration/fallback status if available

### 5. Harden coordinate transformation

Ensure map transforms:

- handle non-finite coordinates
- clamp or safely drop impossible values
- preserve finite valid points
- do not invert axes inconsistently across maps
- are covered by tests for at least Dust2 plus two additional maps

Avoid changing parser output shape unless needed. Keep replay contract backward compatible.

### 6. Update UI without broad redesign

Keep the existing Demo Detail layout.

Small UI additions are acceptable:

- map display name
- calibrated/fallback badge
- missing radar/fallback note

Do not redesign the replay page, coaching panel, dashboard, or render operator flow.

### 7. Update docs

Update:

- `README.md`
- `AGENTS.md`
- `frontend/public/maps/ATTRIBUTION.md`
- optional focused doc if useful

Document:

- supported maps
- how to add a map
- where calibration metadata lives
- fallback behavior
- asset attribution/licensing notes

## Preserve

Do not regress:

- `./scripts/verify.sh`
- cloud preview smoke
- sample demo smoke
- mock upload
- real `.dem` upload
- parser reliability v2 failure handling
- Demo Library search/filter/sort
- rename/archive
- dev owner boundary
- storage boundary compatibility
- round review
- parser event markers
- rules analyzer v3
- coaching UI
- tactical map sync
- Generate Clip / `render_clip`
- RenderOperatorPanel
- fake/manual render-worker callback flows

## Tests

Add focused tests.

Required coverage:

- map config contains all required maps
- unknown map uses explicit fallback, not Dust2 transform
- coordinate transforms differ where expected across maps
- invalid coordinates are handled safely
- replay contract remains backward compatible for old blobs without calibration metadata
- frontend helper returns display/fallback metadata correctly

Use frontend helper tests if `frontend/lib/map-config.ts` or replay helpers change.

## Verification

Run the relevant checks before committing:

```bash
./scripts/verify.sh
docker compose build
docker compose up -d
curl http://localhost:8000/health
python3 scripts/cloud_preview_smoke.py
```

If a local sample demo is available, also run:

```bash
SAMPLE_DEMO_PATH=/absolute/path/to/sample.dem python3 scripts/cloud_preview_smoke.py
```

Manual smoke:

- dashboard loads
- mock upload still works
- real/sample `.dem` upload parses when configured
- demo detail opens
- tactical map shows map display/calibration status
- replay/map/timeline sync still works
- coaching click-to-seek still works
- Generate Clip still creates a render job
- desktop/mobile views have no console errors

## Completion

Commit and push to `origin/main` with a concise imperative commit message.
