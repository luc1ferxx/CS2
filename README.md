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

## Real Demo Parser Spike

The real parser path is intentionally narrow:

1. Open `/dashboard`.
2. Click `Demo Upload`.
3. Choose a `.dem` or `.zip` file.
4. The frontend calls `POST /uploads/demo`.
5. The API validates extension and size, writes the file to `/data/uploads`, creates a `real_parse` job, and enqueues it in Redis.
6. The worker updates status from `queued` to `parsing` to `analyzing` to `completed`.
7. The parser tries `demoparser2`, extracts map name, tick rate, rounds, players, sampled player positions, and kill/death events.
8. The normalizer writes the current replay JSON contract to `/data/replays`.
9. The worker runs deterministic rules-based coaching v2 and inserts only coaching event rows into PostgreSQL.

CLI equivalent:

```bash
curl -F "file=@sample-demos/the-mongolz-vs-liquid-ancient.dem" \
  http://localhost:8000/uploads/demo
```

If `demoparser2` is missing or fails on a demo, the worker marks `demo.status = failed` and writes the parser error to `demo.error_message`. Parser failures do not run the rules analyzer.

## Rules-Based Coaching V2

Real parser output now gets a deterministic, explainable rules pass after replay normalization. This is not OpenAI and does not generate AI prose; each coaching event is built from fixed thresholds and replay facts, then stored in `coaching_events`.

Current rules:

- `untraded_death`: flags a death when no teammate trades a same-area enemy within 5 seconds.
- `isolated_entry`: flags the first T death in a round when the nearest teammate is too far away to trade.
- `poor_spacing`: flags one stretched or overly stacked side spacing moment per round/side.
- `post_plant_spread_issue`: flags planted-bomb frames where multiple alive Ts stay tightly clustered for several seconds.
- `retake_desync`: flags planted-bomb frames where CTs reach the bomb area several seconds apart.

The analyzer reads replay JSON `rounds`, `frames`, `players`, `kills`, `deaths`, and frame-level `bombState`, but only writes compact coaching event rows. Large frame payloads stay in `/data/replays`.

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

Events are de-duped when the same round, player, category, and near tick would otherwise produce repeated cards. Output is sorted by severity first, then tick, so review starts with the highest-signal issues. Event metadata includes `ruleId`, `involvedPlayerIds`, `evidenceTicks`, and rule-specific fields such as `distance`, `windowSeconds`, and `nearbyCount`.

Known limitations:

- Parser frames are sampled, not full tick density, so distances and timing are approximate.
- Coordinates are normalized for the tactical map and only Dust2 has map-specific overview calibration today.
- Utility, line-of-sight, economy, and economy-aware round context are not modeled yet.
- Real parser bomb state is currently best-effort. `post_plant_spread_issue` and `retake_desync` run only when replay frames include planted bomb position data; otherwise they skip without failing the parse.

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
- `GET /render-worker/jobs/{job_id}/manifest`
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
- Deterministic rules-based coaching events for first-pass trading, entry spacing, team spacing, post-plant clustering, and retake timing signals
- `de_dust2` uses the CS2 overview transform (`pos_x=-2476`, `pos_y=3239`, `scale=4.4`) and a real CS2 radar image in `frontend/public/maps/de_dust2_radar.png`

Not supported yet:

- `.rar` extraction
- Full tick-by-tick replay at original demo density
- Automatic CS2 first-person rendering
- OpenAI coaching copy

Next parser work should add upload sessions, S3/R2 quarantine storage, stricter zip inspection, parser telemetry, map-specific coordinate calibration, utility extraction, line-of-sight checks, economy context, and more reliable bomb plant/defuse event parsing.

## Tactical Map Assets

The replay UI supports map-specific radar backgrounds. `de_dust2` currently uses:

- Image: `frontend/public/maps/de_dust2_radar.png`
- Attribution: `frontend/public/maps/ATTRIBUTION.md`
- Coordinate transform: `backend/app/parser/normalizer.py`

CS2 radar images are square assets from `panorama/images/overheadmaps`; overview values come from `resource/overviews/{map}.txt`. For production, extract these from the operator's CS2 install with Source 2 Viewer or replace them with internally licensed assets, then keep the parser normalizer and frontend map image table in sync.

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

POST /render-worker/jobs/{job_id}/result
  -> marks the job completed or failed
  -> completed updates replay.video so FirstPersonReplay can play the mp4/HLS URL
  -> failed preserves existing manual_upload metadata
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
