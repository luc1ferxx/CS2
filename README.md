# CS2 Demo AI Coach Mock MVP

This is a runnable MVP for a website-based CS2 demo AI coach with a mock flow and an early real `.dem` parser spike.

The current build does not call OpenAI, does not use S3/R2, and does not render real CS2 first-person video. The `Mock Upload` button still creates a synthetic demo. The `Demo Upload` button accepts a local `.dem` or `.zip`, queues a parser job, and tries to normalize sampled CS2 positions into the same replay JSON contract used by the frontend.

## Stack

- Frontend: Next.js + TypeScript
- Backend: FastAPI + SQLAlchemy
- Database: PostgreSQL
- Queue: Redis list consumed by a simple Python worker
- Replay blob boundary: local JSON files in `/data/replays` shared by the API and worker containers
- Demo upload boundary: local files in `/data/uploads` shared by the API and worker containers
- Parser spike: `demoparser2` in the worker container
- First-person media boundary: replay JSON includes `video` metadata; manually uploaded local `.mp4` files can be bound and calibrated, but no CS2 video is rendered automatically in this mock phase
- Manual video boundary: local mp4 files in `/data/videos` exposed by the API at `/media/videos/...`
- Render clip boundary: `POST /demos/{demo_id}/render/clip` creates a compact `demo_jobs.job_type = render_clip` row for a short POV/tick range; Render Worker V1 endpoints expose a manifest and callback contract for a future GPU worker
- Render worker runner skeleton: `render-worker/runner.py` can fetch manifests, dry-run a plan, upload a dev fake mp4, prepare CS2 manual probe workspaces, and call the existing completed/failed callback without launching CS2/Steam/OBS/ffmpeg
- Deployment shape: Docker Compose with `frontend`, `api`, `worker`, `postgres`, and `redis`

## Start

```bash
docker compose up --build
```

Then open:

- Frontend: http://localhost:3000
- Backend: http://localhost:8000
- Health check: http://localhost:8000/health

## Mock Flow

1. Open the frontend.
2. The homepage redirects to `/dashboard`.
3. Click `Mock Upload`.
4. The frontend calls `POST /uploads/mock`.
5. The API creates a `demo` and `demo_job` record, then enqueues the job in Redis.
6. The worker updates status from `queued` to `parsing` to `analyzing` to `completed`.
7. The worker writes replay frames plus mock video metadata to a local JSON blob and inserts coaching events in PostgreSQL.
8. The dashboard polls `GET /demos` and shows the completed demo.
9. Open the demo detail page to use the first-person replay shell, tactical map companion, timeline controls, round selector, speed selector, and tick-linked coaching panel.

## Demo Library V1

`/dashboard` is now a compact Demo Library for managing multiple demos, not a development-only test table. It keeps polling processing state while giving users library controls for review workflows.

The library supports:

- Search across demo name, original filename, and map.
- Status and map filters, plus sorting by recently uploaded, name, map, or status.
- Visible parse/coaching/review counts, parse error messages, and compact render/video state derived from existing replay and render job metadata.
- Inline rename through `PATCH /demos/{demo_id}`.
- Soft archive through `POST /demos/{demo_id}/archive`; archived demos are hidden from `GET /demos` by default and can be returned with `includeArchived=true`.
- Mock Upload and Real Demo Upload actions with queued status feedback while the existing polling flow updates the list.

Current limitations:

- Archive is a soft hide only. It does not delete uploaded `.dem`, replay JSON, videos, jobs, or coaching rows.
- The dashboard derives render readiness from current replay video metadata and latest `render_clip` job state; it does not render footage itself.
- Date filtering is not exposed yet beyond recent/uploaded sorting.

## Real Demo Parser Spike

The real parser path is intentionally narrow:

1. Open `/dashboard`.
2. Click `Demo Upload`.
3. Choose a `.dem` or `.zip` file.
4. The frontend calls `POST /uploads/demo`.
5. The API validates extension and size, writes the file to `/data/uploads`, creates a `real_parse` job, and enqueues it in Redis.
6. The worker updates status from `queued` to `parsing` to `analyzing` to `completed`.
7. The parser tries `demoparser2`, extracts map name, tick rate, rounds, players, sampled player positions, kill/death rows, and best-effort bomb/utility events.
8. The normalizer writes the current replay JSON contract plus compact `events` to `/data/replays`.
9. The worker runs deterministic rules-based coaching v2 and inserts only coaching event rows into PostgreSQL.

CLI equivalent:

```bash
curl -F "file=@sample-demos/the-mongolz-vs-liquid-ancient.dem" \
  http://localhost:8000/uploads/demo
```

If `demoparser2` is missing or fails on a demo, the worker marks `demo.status = failed` and writes the parser error to `demo.error_message`. Parser failures do not run the rules analyzer.

## Rules-Based Coaching V3

Real parser output now gets a deterministic, explainable rules pass after replay normalization. This is not OpenAI and does not generate AI prose; each coaching event is built from fixed thresholds and replay facts, then stored in `coaching_events`.

Current rules:

- `untraded_death`: flags a death when no teammate trades a same-area enemy within 5 seconds.
- `isolated_entry`: flags the first T death in a round when the nearest teammate is too far away to trade.
- `poor_spacing`: flags one stretched or overly stacked side spacing moment per round/side.
- `post_plant_spread_issue`: flags planted-bomb frames where multiple alive Ts stay tightly clustered for several seconds.
- `retake_desync`: flags planted-bomb frames where CTs reach the bomb area several seconds apart.
- `weak_utility_before_execute`: uses parser `bomb_planted` and utility events to flag plants with fewer than two T-side utility events in the prior 12 seconds.
- `late_post_plant_utility`: uses parser `bomb_planted` and utility events to flag the first T-side post-plant utility that arrives more than 6 seconds after the plant.
- `post_plant_spacing_with_bomb_event`: anchors the post-plant clustering signal to a parser `bomb_planted` event and records the related bomb event id/tick.

The analyzer reads replay JSON `rounds`, `frames`, `players`, `kills`, `deaths`, frame-level `bombState`, and compact parser `events`, but only writes compact coaching event rows. Parser event-backed rules are best-effort and skip cleanly when their required event family is missing. Large frame payloads stay in `/data/replays`.

Rules are configured through `backend/app/analysis/rules.py::RuleConfig`. The current defaults are:

- `trade_window_seconds = 5`
- `same_area_distance = 12`
- `isolated_teammate_distance = 22`
- `poor_spacing_min_distance = 2.5`
- `poor_spacing_max_distance = 28`
- `max_events_per_round_per_rule = 1`
- `dedupe_tick_window_seconds = 3`
- `post_plant_cluster_distance = 6`
- `post_plant_min_duration_seconds = 4`
- `retake_site_distance = 12`
- `retake_desync_seconds = 4`
- `execute_utility_window_seconds = 12`
- `min_execute_utility_events = 2`
- `post_plant_utility_grace_seconds = 6`

Events are de-duped when the same round, player, category, and near tick would otherwise produce repeated cards. Output is sorted by severity first, then tick, so review starts with the highest-signal issues. Event metadata includes `ruleId`, `involvedPlayerIds`, `evidenceTicks`, and rule-specific fields such as `relatedEventIds`, `distance`, `windowSeconds`, `nearbyCount`, utility event labels/types, and bomb event labels/types.

## Coaching Review UI V3

The demo detail coaching panel is a review tool for deterministic event rows, not an OpenAI chat surface. It supports:

- Round-grouped coaching events across the full demo, with the selected round highlighted.
- Severity filtering for `all`, `high`, `medium`, and `low` where `critical` is included with high and `info` is included with low.
- Rule filtering for current rules such as `untraded_death`, `isolated_entry`, `poor_spacing`, `post_plant_spread`, `retake_desync`, `weak_utility_before_execute`, `late_post_plant_utility`, and `post_plant_spacing_with_bomb_event`. The UI treats stored `post_plant_spread_issue` rows as the `post_plant_spread` filter.
- Search across player names, event title, event description, rule id, rule label, and summarized evidence metadata.
- Dense event cards showing severity, rule id/label, round, tick, involved players, short explanation, and evidence metadata such as `relatedEventIds`, `distance`, `windowSeconds`, `evidenceTicks`, `nearbyCount`, utility details, bomb details, and `site`.
- Tick-linked coaching markers on the timeline. Marker position is derived from `tick_start` within the current round and marker color follows severity.
- Compact parser event markers on the timeline for kills, bomb plant/defuse/explode, and utility events. These sit in their own marker row so they do not cover the slider or coaching markers.
- Event-level `Generate Clip for this event`, which calls the existing `render_clip` API and refreshes the Render Operator panel state.

## Round Review UX V1

Demo Detail includes a compact round review panel between the tactical map and render/operator tools. It is a derived frontend view over replay `rounds`, compact parser `events`, and stored coaching events; it does not require backend or parser changes.

The round review panel supports:

- A dense round list showing round number, winner side, tick range, kill count, bomb event count, utility event count, coaching event count, selected state, and current-round state.
- A selected-round summary showing winner side, start tick, live/freeze-end tick, end tick, first kill tick/player, bomb plant tick/site, and counts for kills, utility, and coaching events.
- Quick jumps for round start, live start, first kill, and bomb plant. Missing first-kill or plant data disables only that jump.
- Shared synchronization with the existing Demo Detail state: selecting a round updates the replay tick, and timeline seeks, coaching card seeks, and parser marker seeks continue updating the selected round.

Known limitations:

- Round review counts depend on compact parser events. If a demo lacks a kill, bomb, or utility event family, the affected count or quick jump is empty rather than inferred from raw parser data.
- First-kill and bomb-plant labels use best-effort parser event metadata.
- Round list counts are compact scan aids, not strategic scoring or AI-generated analysis.

Known limitations:

- Parser frames are sampled, not full tick density, so distances and timing are approximate.
- Tactical map coordinates are map-specific for the supported pool, but only Dust II is calibrated from CS2 overview values today. Mirage, Inferno, Ancient, Nuke, and Anubis use approximate bounds and are labeled as such in the replay UI.
- Line-of-sight, economy, and economy-aware round context are not modeled yet.
- Real parser bomb state and utility events are best-effort. Missing bomb/utility event families are tolerated and do not fail a parse or analysis run. `post_plant_spread_issue` and `retake_desync` run only when replay frames include planted bomb position data; event-backed utility/bomb rules skip when their parser events are absent.
- Trade tagging in parser `events` is not enabled yet. Existing rules still infer trade windows from kill/death rows during analysis.

## Product Direction: Demo-First Review

The intended user workflow is `.dem` first, not `.dem + mp4`.

Primary user flow:

```text
user uploads .dem
  -> backend parses rounds, players, positions, kills, deaths, bomb state
  -> rules analyzer generates deterministic coaching events
  -> website immediately shows Web Replay, tactical map, round navigation, timeline, and coaching
  -> user clicks a coaching event or round
  -> if a rendered clip already exists, first-person video plays
  -> if no clip exists, user can request Generate Clip for that event/player/tick range
```

This means the core product must remain useful immediately after `.dem` upload even when no real video exists. The browser can show tactical replay and coaching from parsed data, but it cannot directly play `.dem` as CS2 first-person video because `.dem` files are game state/event recordings, not video streams.

Real CS2 first-person footage should be an asynchronous enhancement:

```text
Generate Clip
  -> create render_clip job with demoId, eventId, playerId, tickStart, tickEnd
  -> our Windows/Linux GPU render worker downloads the .dem
  -> worker runs CS2 in our controlled environment
  -> worker renders the selected POV around the event, for example tick +/- 20 seconds
  -> worker uploads mp4/HLS to object storage
  -> API writes video metadata back into the replay/media contract
  -> frontend plays the generated clip in sync with map and coaching
```

The render worker must run on infrastructure we control. The web app should not ask for permission to control the user's computer, open their local CS2 client, read local files after upload, or record their screen. Users should only upload `.dem` files and interact with the website.

## Render Clip Job V1

Render Clip Job V1 is a backend/frontend boundary only. It does not render real CS2 footage yet.

Current behavior:

1. A user clicks `Generate Clip` on a coaching event or requests a clip around the selected tick.
2. The frontend sends `POST /demos/{demo_id}/render/clip` with optional `eventId`, optional `playerId` or `povSteamId`, `tickStart`, `tickEnd`, `tickRate`, optional `roundNumber`, and optional `renderPreset`.
3. The API validates that the demo and replay blob exist, that `tickEnd > tickStart`, and that the requested duration is at most `MAX_RENDER_CLIP_SECONDS` seconds, defaulting to 60.
4. The API creates a compact `demo_jobs` row with `job_type = render_clip`, `status = queued`, and JSON metadata describing only the event/player/tick range. Large demo, frame, and media payloads stay out of PostgreSQL.
5. The local worker recognizes `render_clip`, moves the job from `queued` to `rendering`, then marks it `failed` with: `Render clip worker is not connected yet. A Windows/Linux GPU worker must process this job.`
6. Render Worker V1 endpoints can expose the job manifest and accept a future worker callback that marks the job completed or failed.
7. Manual `source = manual_upload` video metadata is left intact on failures. If no manual video is bound, the replay video status may show queued/rendering/failed while the first-person fallback shell stays available.
8. The demo detail page shows a compact internal Render Operator panel with the latest `render_clip` job, requested tick range, event/player/POV metadata, current video source/status, output URL when available, and a refresh action.

V1 intentionally fails because the API container must not run CS2, OBS, ffmpeg, OpenAI, or object-storage automation. The failure is the contract marker for a future external render worker, not an application error.

Future worker contract:

```text
input:  .dem source, POV player or Steam ID, tickStart, tickEnd, tickRate, renderPreset
output: mp4/HLS URL, durationSeconds, tickStart, tickEnd, tickRate, source = rendered, timeOriginSeconds/calibration metadata
```

The final product should not require users to upload MP4 files. Manual MP4 upload remains only a development and QA bridge for validating media synchronization before the render worker exists.

## Render Worker V1 Contract

Render Worker V1 is an adapter boundary for a future Windows/Linux GPU worker. It still does not launch CS2, record video, call ffmpeg, call OpenAI, or use S3/R2.

The boundary is token-gated for local development with `X-Render-Worker-Token`. The default token is `dev-render-worker-token`; override it with `RENDER_WORKER_TOKEN` outside local development. This is not production authentication.

Manifest endpoint:

```bash
curl http://localhost:8000/render-worker/jobs/{job_id}/manifest \
  -H "X-Render-Worker-Token: dev-render-worker-token"
```

Next queued manifest endpoint:

```bash
curl http://localhost:8000/render-worker/jobs/next \
  -H "X-Render-Worker-Token: dev-render-worker-token"
```

If no queued `render_clip` job exists, this returns `204 No Content`.

Recent render jobs for a demo:

```bash
curl http://localhost:8000/demos/{demo_id}/render/jobs
```

The response is read-only and includes recent `render_clip` jobs with job status, timestamps, error message, requested tick range, event/player/POV metadata, render preset, and current video source/status. Local render-worker workspace paths are not returned because the API container does not know where an operator prepared a job.

Manifest shape:

```json
{
  "manifestVersion": "render_worker_v1",
  "jobId": "job_123",
  "demoId": "demo_123",
  "jobType": "render_clip",
  "status": "queued",
  "demoFilePath": "/data/uploads/demo_123/source.dem",
  "demoStorageKey": "local://uploads/demo_123/source.dem",
  "originalFilename": "source.dem",
  "mapName": "de_dust2",
  "eventId": "event_123",
  "playerId": "7656119...",
  "povSteamId": null,
  "tickStart": 640,
  "tickEnd": 3200,
  "tickRate": 64,
  "roundNumber": 3,
  "renderPreset": "event_clip_v1"
}
```

Result callback:

```bash
curl -X POST http://localhost:8000/render-worker/jobs/{job_id}/result \
  -H "X-Render-Worker-Token: dev-render-worker-token" \
  -H "Content-Type: application/json" \
  -d '{
    "status":"completed",
    "videoUrl":"/media/videos/demo_123/rendered.mp4",
    "tickStart":640,
    "tickEnd":3200,
    "tickRate":64,
    "timeOriginSeconds":0,
    "durationSeconds":40,
    "errorMessage":null
  }'
```

Completed output may use `videoUrl` or `localMediaPath`. A `localMediaPath` must either be a `/media/videos/...` URL path or an absolute path under the configured video storage directory. On completion, the API marks the job `completed` and writes replay `video` metadata with `status = ready`, `source = rendered`, the clip URL, tick range, tick rate, duration, and `timeOriginSeconds`.

Dev media upload endpoint:

```bash
curl -X POST http://localhost:8000/render-worker/jobs/{job_id}/media \
  -H "X-Render-Worker-Token: dev-render-worker-token" \
  -F "file=@dev-placeholder.mp4"
```

This stores a small local mp4 under `/data/videos/{demo_id}/` and returns a `/media/videos/...` URL for the result callback. It does not store video bytes in PostgreSQL and does not change replay metadata by itself.

Failure callback:

```json
{
  "status": "failed",
  "videoUrl": null,
  "localMediaPath": null,
  "tickStart": 640,
  "tickEnd": 3200,
  "tickRate": 64,
  "timeOriginSeconds": 0,
  "durationSeconds": 40,
  "errorMessage": "Renderer timed out before capture completed"
}
```

On failure, the API marks the job `failed` and records the error. Existing `source = manual_upload` video metadata is preserved so QA/manual calibration is not destroyed by a render failure.

Future GPU worker integration should:

1. Poll or claim `render_clip` jobs.
2. Fetch the manifest through the token-gated endpoint.
3. Resolve `demoFilePath` or the future object-storage key.
4. Run CS2 only on controlled Windows/Linux GPU infrastructure.
5. Render the selected POV/tick range.
6. Upload or place mp4/HLS output.
7. POST the result callback so the replay video contract becomes playable by the existing frontend.

## Render Worker Runner Skeleton

`render-worker/runner.py` is a local/dev skeleton for the future external GPU worker. It uses the same token-gated Render Worker V1 API and deliberately performs no real rendering.

It is adapter-based:

- `FakeVideoAdapter` preserves the dev fake mp4 upload and callback flow.
- `CS2ManualAdapter` prepares a manual render workspace and callback metadata for an operator. It does not start Steam, start CS2, run `playdemo`, call OBS, or call ffmpeg.

Environment:

```bash
export API_BASE_URL=http://localhost:8000
export RENDER_WORKER_TOKEN=dev-render-worker-token
export WORK_DIR=.render-worker-work
export POLL_INTERVAL_SECONDS=5
export DEV_FAKE_VIDEO_PATH=/absolute/path/to/dev-placeholder.mp4
export CS2_INSTALL_DIR="/absolute/path/to/Counter-Strike Global Offensive"
export STEAM_USER_DATA_DIR=/absolute/path/to/Steam/userdata
export CS2_MANUAL_OUTPUT_FILENAME="{job_id}.mp4"
```

Commands:

```bash
python3 render-worker/runner.py dry-run {job_id}
python3 render-worker/runner.py dry-run
python3 render-worker/runner.py process-job {job_id}
python3 render-worker/runner.py poll-once
python3 render-worker/runner.py prepare-job --job-id {job_id} --adapter cs2-manual
python3 render-worker/runner.py complete-prepared-job --job-id {job_id}
python3 render-worker/runner.py complete-prepared-job --job-id {job_id} --video-path /absolute/path/to/clip.mp4
```

`dry-run` fetches a manifest and writes a local manifest snapshot under `WORK_DIR`, then prints the plan without upload or callback. `process-job` fetches one known manifest. `poll-once` asks the API for the next queued `render_clip` manifest.

If `DEV_FAKE_VIDEO_PATH` points at an existing local `.mp4`, the runner uploads it to the dev media endpoint and posts a completed result callback with the returned `/media/videos/...` URL. The frontend then plays that URL through the existing `FirstPersonReplay` branch. If `DEV_FAKE_VIDEO_PATH` is missing or invalid, the runner posts a failed callback explaining that the real renderer is not connected; existing `manual_upload` video metadata is preserved by the API.

`prepare-job --adapter cs2-manual` creates `{WORK_DIR}/jobs/{job_id}/manifest.json`, `instructions.md`, `expected_output.json`, and an `output/` directory. The instructions list the demo path/reference, POV player or Steam ID, tick range, round, recommended mp4 path, and manual steps for a controlled render operator. `complete-prepared-job` uploads the expected or supplied mp4 and posts a completed callback. If the mp4 does not exist, it returns a waiting state and does not mark the job completed.

The manual adapter also writes `status.json` with `preparedAt`, `expectedVideoPath`, `state = waiting_for_manual_recording`, and the next runner action. A MacBook development machine can verify the full contract with fake/manual MP4 files, but it cannot perform real CS2 rendering.

See `render-worker/README.md` for the full skeleton workflow and replacement path for a real controlled GPU adapter.

## Manual Video Binding And Sync Calibration

Manual mp4 binding is a development and QA bridge, not the target user workflow.
It exists to validate the media contract, calibration UI, and tick/video synchronization before the GPU render worker is available. Production users should not be expected to record and upload their own mp4 files.

After a demo parse completes, open the demo detail page and use `Video Setup / Sync Calibration`:

1. Upload a manually prepared `.mp4`.
2. The frontend calls `POST /demos/{demo_id}/video/upload`.
3. The API validates `.mp4` extension and size, stores the file under `/data/videos/{demo_id}/`, and writes only media metadata into the replay JSON blob.
4. The video becomes playable through `/media/videos/{demo_id}/{file}` and `GET /demos/{demo_id}/video` returns `source = manual_upload` and `status = ready`.
5. Enter `timeOriginSeconds`, `tickStart`, and `tickEnd`, or click `Use current video time as tickStart origin`.
6. Click `Save Calibration`, which calls `POST /demos/{demo_id}/video/calibration` and updates the replay JSON `video` metadata.

CLI equivalent for video upload:

```bash
curl -F "file=@falcons-vs-furia-m1-dust2.mp4" \
  http://localhost:8000/demos/{demo_id}/video/upload
```

CLI equivalent for calibration:

```bash
curl -X POST http://localhost:8000/demos/{demo_id}/video/calibration \
  -H "Content-Type: application/json" \
  -d '{"timeOriginSeconds":12.5,"tickStart":12345,"tickEnd":54321}'
```

The tick/video mapping is:

```text
tickToVideoTime(tick) = timeOriginSeconds + (tick - tickStart) / tickRate
videoTimeToTick(time) = tickStart + (time - timeOriginSeconds) * tickRate
```

The frontend clamps both directions to keep the first-person video, timeline, tactical map, and coaching panel on the same tick. Old replay blobs that do not include `timeOriginSeconds` are treated as `0`.

## API

- `GET /health`
- `GET /demos`
- `POST /uploads/mock`
- `POST /uploads/demo`
- `GET /demos/{demo_id}/status`
- `GET /demos/{demo_id}/replay`
- `GET /demos/{demo_id}/coaching`
- `GET /demos/{demo_id}/video`
- `POST /demos/{demo_id}/video/upload`
- `POST /demos/{demo_id}/video/calibration`
- `POST /demos/{demo_id}/render/mock`
- `POST /demos/{demo_id}/render/clip`
- `GET /demos/{demo_id}/render/jobs`
- `GET /render-worker/jobs/next`
- `GET /render-worker/jobs/{job_id}/manifest`
- `POST /render-worker/jobs/{job_id}/media`
- `POST /render-worker/jobs/{job_id}/result`

## Development Notes

There is no real authentication in this phase. The backend uses a fixed development user id.

PostgreSQL stores only metadata and indexed coaching events:

- `demos`
- `demo_jobs`
- `coaching_events`

Replay frame data is treated as blob-style storage and written to shared JSON files. Uploaded demos are also local files in the Docker volume during this spike. This keeps the boundary ready for S3/R2 later and avoids putting large tick payloads in PostgreSQL.

Video files are not stored in PostgreSQL. Local manual mp4 files live under `/data/videos` in Docker Compose. The current replay contract only carries media metadata such as `video.status`, `video.url`, `video.durationSeconds`, tick range, tick rate, source, optional `errorMessage`, and `timeOriginSeconds`. Real mp4/HLS assets should live in object storage later.

## First-person Playback Reality Check

Browsers cannot directly play CS2 `.dem` files. A `.dem` is not a video stream, and this MVP does not try to render CS2 first-person gameplay in the browser.

The current first-person player is still a mock/player shell when `video.url` is `null`; the frontend renders a styled mock first-person viewport and synchronizes it with the same `currentTick` used by coaching events and the tactical map. For real parsed demos, the tactical map uses sampled real player coordinates. If an operator manually uploads an mp4, the browser plays that mp4 and uses the saved calibration metadata to map video time to demo ticks.

Manual mp4 binding is not automatic CS2 rendering. The API does not run CS2, OBS, ffmpeg, or OpenAI. A local mp4 such as `falcons-vs-furia-m1-dust2.mp4` can be used for validation if it exists in the checkout, but the code does not depend on that file and mp4 files should not be committed. Manual clips must be calibrated only to the tick range they actually cover; a 46 second clip cannot represent a full 54 minute demo.

The production path should be:

```text
upload .dem
  -> Docker API creates a job
  -> independent Windows/Linux GPU render worker runs the CS2 client
  -> worker executes playdemo
  -> worker captures and transcodes mp4/HLS
  -> rendered media is stored in S3/R2 or equivalent object storage
  -> frontend plays the video and synchronizes coaching via tick <-> video time
```

## Parser Spike Scope

Current support:

- `.dem` uploads and `.zip` uploads containing at least one `.dem`
- Extension validation and 1 GiB upload size limit
- Local upload storage only, no S3/R2
- `demoparser2==0.41.0`, selected because it has Python 3.12 Linux wheels for the backend Docker image
- Best-effort extraction of map, tick rate, rounds, roster, sampled positions, and kill/death rows
- Existing replay JSON contract, so the current Demo Detail page can open parser output
- Deterministic rules-based coaching events for first-pass trading, entry spacing, team spacing, post-plant clustering, retake timing, weak execute utility, and late post-plant utility signals
- Map metadata in replay JSON for tactical map rendering, including radar image path, transform, attribution source, and calibration confidence
- Parser Data Quality v1 compact replay events in `replay.events`

Not supported yet:

- `.rar` extraction
- Full tick-by-tick replay at original demo density
- Automatic CS2 first-person rendering
- OpenAI coaching copy

Next parser work should add upload sessions, S3/R2 quarantine storage, stricter zip inspection, parser telemetry, stronger map-specific coordinate calibration, richer utility trajectory/context extraction, line-of-sight checks, economy context, and more reliable bomb plant/defuse event parsing.

## Parser Data Quality V1

Parser Data Quality v1 adds a lightweight, backward-compatible replay event contract. Old replay blobs that do not include `events` are treated as `events: []`; mock replays include representative events so the frontend can use one path.

Replay event shape:

```json
{
  "id": "kill-1234-t1-ct1",
  "type": "kill",
  "tick": 1234,
  "roundNumber": 3,
  "playerId": "7656119...",
  "playerName": "player.name",
  "side": "T",
  "x": 42.5,
  "y": 61.2,
  "label": "player.name killed defender",
  "metadata": {
    "victimId": "7656119...",
    "weapon": "ak47"
  }
}
```

Supported event types:

- `kill`
- `death`
- `bomb_planted`
- `bomb_defused`
- `bomb_exploded`
- `smoke`
- `flash`
- `molotov`
- `he`

Reliable fields today:

- `tick`
- `roundNumber` when round metadata or `total_rounds_played` is available
- kill attacker/victim IDs and names where `player_death` exposes them
- round start/end ticks where `round_start` and `round_end` are available
- player positions from sampled tick frames

Best-effort fields:

- `freezeEndTick`, winner side, and winner reason because event availability varies by demo/parser output
- bomb plant/defuse/explode site/player/position
- utility thrower and landing position
- kill assister, headshot, weapon, and side metadata
- map event positions, which are transformed through centralized map config when source coordinates are available

Current demoparser2 limitations:

- Event families may be absent or use different field names across demos.
- Utility and bomb events can be missing while player frames and kill rows still parse correctly.
- Trade metadata is not emitted in `replay.events`; analyzer rules still perform their own best-effort trade-window inference from kill/death rows.
- Full tick density is not stored. Replay frames remain sampled and compact.

Parser/normalizer behavior must stay tolerant: one missing event family should not fail the whole demo parse, and large raw parser dataframes or unbounded JSON must not be stored in PostgreSQL or replay blobs.

## Tactical Map Assets

The replay UI supports map-specific radar backgrounds through centralized map config in `backend/app/parser/map_config.py` and `frontend/lib/map-config.ts`.

Supported maps:

| Map | Radar image | Coordinate status |
| --- | --- | --- |
| `de_dust2` | `frontend/public/maps/de_dust2_radar.png` | Calibrated with CS2 overview transform (`pos_x=-2476`, `pos_y=3239`, `scale=4.4`) |
| `de_mirage` | `frontend/public/maps/de_mirage_radar.png` | Approximate bounds |
| `de_inferno` | `frontend/public/maps/de_inferno_radar.png` | Approximate bounds |
| `de_ancient` | `frontend/public/maps/de_ancient_radar.png` | Approximate bounds |
| `de_nuke` | `frontend/public/maps/de_nuke_radar.png` | Approximate bounds; lower radar image is stored for future floor-aware rendering |
| `de_anubis` | `frontend/public/maps/de_anubis_radar.png` | Approximate bounds |

Unknown maps use a generated fallback grid with `confidence: fallback`; they do not reuse the Dust II radar image or transform. Old replay blobs that only include `mapName` still resolve known map images on the frontend.

Attribution:

- Radar assets: `frontend/public/maps/ATTRIBUTION.md`
- Backend replay metadata: `mapMetadata` in the replay JSON includes `mapName`, `displayName`, `radarImagePath`, `calibrated`, `confidence`, `source`, and `transform`.

CS2 radar images are square assets from `panorama/images/overheadmaps`; overview values come from `resource/overviews/{map}.txt`. For production, extract these from the operator's CS2 install with Source 2 Viewer or replace them with internally licensed assets, then keep the backend and frontend map config files in sync.

## Future Phase: External Render Worker

The current API/worker supports a mock render job, a real `render_clip` job boundary, and the Render Worker V1 manifest/callback adapter:

```text
POST /demos/{demo_id}/render/mock
  -> creates demo_jobs.job_type = mock_render
  -> sets replay.video.status = queued
  -> worker sets queued -> rendering -> ready
  -> replay.video.url remains null
  -> FirstPersonReplay keeps using the mock/player shell

POST /demos/{demo_id}/render/clip
  -> creates demo_jobs.job_type = render_clip with compact tick-range metadata
  -> worker sets queued -> rendering -> failed
  -> failure says the Windows/Linux GPU worker is not connected yet
  -> existing manual_upload video metadata is not cleared

GET /render-worker/jobs/{job_id}/manifest
  -> returns demo file reference, POV, tick range, map, and preset

GET /render-worker/jobs/next
  -> returns the oldest queued render_clip manifest, or 204 when none exists

POST /render-worker/jobs/{job_id}/media
  -> dev-only mp4 ingestion for a skeleton/fake render output
  -> stores bytes under /data/videos, not in Postgres

POST /render-worker/jobs/{job_id}/result
  -> marks the job completed or failed
  -> completed updates replay.video so FirstPersonReplay can play the mp4/HLS URL
  -> failed preserves existing manual_upload metadata

render-worker CS2ManualAdapter
  -> prepare-job writes manifest, instructions, expected output metadata, and output path
  -> operator manually renders on controlled infrastructure
  -> complete-prepared-job uploads the mp4 and uses the same result callback
  -> no Steam/CS2/OBS/ffmpeg automation is run by the skeleton
```

Do not implement real CS2 automation in the API container. The next media-focused spike should connect a separate GPU worker to claim `render_clip` jobs and write rendered video metadata back to the replay contract.

The first useful production-shaped render feature should be clip rendering, not whole-match rendering:

1. User uploads only a `.dem`.
2. Parser and rules analyzer complete Web Replay and coaching.
3. User clicks `Generate Clip` on a coaching event, or selects a player plus tick range.
4. API creates a `render_clip` job.
5. The job input includes `demoId`, optional `eventId`, `povSteamId` or player id, `tickStart`, `tickEnd`, `tickRate`, `mapName`, and render preset.
6. A separate Windows/Linux GPU worker runs CS2, renders only that POV/tick window, uploads mp4/HLS, and writes `video.url`, `durationSeconds`, `tickStart`, `tickEnd`, `tickRate`, `source = rendered`, and `timeOriginSeconds` metadata.
7. Frontend plays the generated clip and keeps first-person video, timeline, tactical map, and coaching on the same tick.

When we are ready to prove real rendering, the end-to-end path is:

1. Start from one known `.dem` file.
2. Run a dedicated Windows or Linux GPU worker with the CS2 client installed.
3. Use `playdemo` to render a deterministic first-person POV for one short tick range.
4. Capture and transcode that clip to mp4 or HLS.
5. Upload the rendered media to object storage.
6. Write `video.url`, `durationSeconds`, `tickStart`, `tickEnd`, and `tickRate` into the replay/media metadata contract.
7. Verify frontend video playback stays synchronized with coaching events and the tactical map.

### Why This Does Not Belong in the API Container

The FastAPI container should not run CS2 rendering. A production render path needs GPU access, a game client runtime, display/audio/capture tooling, large temporary files, crash isolation, and much longer job durations than normal API requests. Putting that inside the API container would make request latency, deploy safety, autoscaling, and security worse.

Recommended shape:

```text
FastAPI API
  -> creates render job metadata
  -> queues render job

GPU render worker
  -> claims one render job
  -> downloads the .dem from object storage
  -> launches CS2 in an isolated worker environment
  -> runs playdemo with deterministic config
  -> captures and transcodes mp4/HLS
  -> uploads media to object storage
  -> updates video metadata
```

### Legacy Contract Sketch

Input contract:

```json
{
  "jobId": "job_123",
  "demoId": "demo_123",
  "demoObjectKey": "uploads/demo_123/source.dem",
  "mapName": "de_inferno",
  "povSteamId": "7656119...",
  "tickStart": 0,
  "tickEnd": 2240,
  "tickRate": 64,
  "renderPreset": "first_person_1080p30"
}
```

Output contract:

```json
{
  "status": "ready",
  "url": "https://media.example.com/demo_123/master.m3u8",
  "durationSeconds": 35,
  "tickStart": 0,
  "tickEnd": 2240,
  "tickRate": 64,
  "timeOriginSeconds": 0,
  "source": "rendered",
  "errorMessage": null
}
```

Failure output:

```json
{
  "status": "failed",
  "url": null,
  "durationSeconds": 0,
  "tickStart": 0,
  "tickEnd": 2240,
  "tickRate": 64,
  "source": "rendered",
  "errorMessage": "Renderer timed out before capture completed"
}
```

### Render Security And Cost Notes

- Treat `.dem` files and zip uploads as untrusted input.
- Run render workers as isolated, replaceable machines or containers with narrow object-storage permissions.
- Do not expose Steam credentials, game tokens, or object storage write credentials to the public API container.
- Enforce per-user render quotas, max demo duration, max concurrent render jobs, and temporary file cleanup.
- Prefer signed URLs for source demo downloads and rendered video playback.
- Store mp4/HLS in object storage with lifecycle policies; keep only metadata in PostgreSQL.
- Track queue depth, render duration, failure rate, GPU utilization, media storage growth, and per-user cost.
- Keep renderer logs scrubbed of secrets and object-storage signed URLs.
