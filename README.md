# CS2 Demo AI Coach Mock MVP

This is a runnable first-phase mock MVP for a website-based CS2 demo AI coach.

The current build does not upload real `.dem` files, does not parse CS2 demos, does not call OpenAI, and does not use S3/R2. The `Mock Upload` button creates a synthetic demo, queues a worker job, generates mock replay frames plus coaching events, and lets the frontend play a first-person replay shell with a synchronized tactical map.

## Stack

- Frontend: Next.js + TypeScript
- Backend: FastAPI + SQLAlchemy
- Database: PostgreSQL
- Queue: Redis list consumed by a simple Python worker
- Replay blob boundary: local JSON files in `/data/replays` shared by the API and worker containers
- First-person media boundary: replay JSON includes `video` metadata, but no real video is rendered in this mock phase
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

## API

- `GET /health`
- `GET /demos`
- `POST /uploads/mock`
- `GET /demos/{demo_id}/status`
- `GET /demos/{demo_id}/replay`
- `GET /demos/{demo_id}/coaching`
- `GET /demos/{demo_id}/video`
- `POST /demos/{demo_id}/render/mock`

## Development Notes

There is no real authentication in this phase. The backend uses a fixed development user id.

PostgreSQL stores only metadata and indexed coaching events:

- `demos`
- `demo_jobs`
- `coaching_events`

Replay frame data is treated as blob-style storage and written to shared JSON files. This keeps the boundary ready for S3/R2 later and avoids putting large tick payloads in PostgreSQL.

Video files are not stored in PostgreSQL. The current replay contract only carries media metadata such as `video.status`, `video.url`, `video.durationSeconds`, tick range, tick rate, source, and optional `errorMessage`. Real mp4/HLS assets should live in object storage later.

## First-person Playback Reality Check

Browsers cannot directly play CS2 `.dem` files. A `.dem` is not a video stream, and this MVP does not try to render CS2 first-person gameplay in the browser.

The current first-person player is a mock/player shell. If `video.url` is `null`, the frontend renders a styled mock first-person viewport and synchronizes it with the same `currentTick` used by coaching events and the tactical map.

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

## Next Phase: Real `.dem` Parser

The next phase should replace `backend/app/services/mock_replay_service.py` with a parser-backed implementation:

1. Add real upload sessions and object storage direct upload.
2. Store original `.dem` or `.zip` files in S3/R2 quarantine storage.
3. Add zip validation, size limits, and safe extraction.
4. Use `demoparser2` or `awpy` inside parser workers.
5. Normalize parser output into the existing replay blob schema.
6. Keep coaching events in PostgreSQL, but store large replay payloads as object storage blobs.
7. Add a rules package for positioning, trading, utility, timing, economy, objective, retake, and post-plant events.
8. Add OpenAI only after rule events exist, passing structured coaching context rather than raw demo data.

## Next Phase: Render Worker Spike

The current API/worker only supports a mock render job:

```text
POST /demos/{demo_id}/render/mock
  -> creates demo_jobs.job_type = mock_render
  -> sets replay.video.status = queued
  -> worker sets queued -> rendering -> ready
  -> replay.video.url remains null
  -> FirstPersonReplay keeps using the mock/player shell
```

Do not implement real CS2 automation in this MVP. The next media-focused spike should prove one end-to-end render path:

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
