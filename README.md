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

CLI equivalent:

```bash
curl -F "file=@sample-demos/the-mongolz-vs-liquid-ancient.dem" \
  http://localhost:8000/uploads/demo
```

If `demoparser2` is missing or fails on a demo, the worker marks `demo.status = failed` and writes the parser error to `demo.error_message`. This spike does not yet generate coaching events from real rule analysis.

## Manual Video Binding And Sync Calibration

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

Manual mp4 binding is not automatic CS2 rendering. The API does not run CS2, OBS, ffmpeg, or OpenAI. A local mp4 such as `falcons-vs-furia-m1-dust2.mp4` can be used for validation if it exists in the checkout, but the code does not depend on that file and mp4 files should not be committed.

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
- `de_dust2` uses the CS2 overview transform (`pos_x=-2476`, `pos_y=3239`, `scale=4.4`) and a real CS2 radar image in `frontend/public/maps/de_dust2_radar.png`

Not supported yet:

- `.rar` extraction
- Full tick-by-tick replay at original demo density
- Rule-generated coaching events for real demos
- Automatic CS2 first-person rendering
- OpenAI coaching copy

Next parser work should add upload sessions, S3/R2 quarantine storage, stricter zip inspection, parser telemetry, map-specific coordinate calibration, and a rules package for positioning, trading, utility, timing, economy, objective, retake, and post-plant events.

## Tactical Map Assets

The replay UI supports map-specific radar backgrounds. `de_dust2` currently uses:

- Image: `frontend/public/maps/de_dust2_radar.png`
- Attribution: `frontend/public/maps/ATTRIBUTION.md`
- Coordinate transform: `backend/app/parser/normalizer.py`

CS2 radar images are square assets from `panorama/images/overheadmaps`; overview values come from `resource/overviews/{map}.txt`. For production, extract these from the operator's CS2 install with Source 2 Viewer or replace them with internally licensed assets, then keep the parser normalizer and frontend map image table in sync.

## Future Phase: GPU Render Worker Spike

The current API/worker only supports a mock render job:

```text
POST /demos/{demo_id}/render/mock
  -> creates demo_jobs.job_type = mock_render
  -> sets replay.video.status = queued
  -> worker sets queued -> rendering -> ready
  -> replay.video.url remains null
  -> FirstPersonReplay keeps using the mock/player shell
```

Do not implement real CS2 automation in this MVP. The next media-focused spike after manual video binding should prove one end-to-end render path:

1. Start from one known `.dem` file.
2. Run a dedicated Windows or Linux GPU worker with the CS2 client installed.
3. Use `playdemo` to render a deterministic first-person POV.
4. Capture and transcode to mp4 or HLS.
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

### Future Render Worker Contract

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
