# Deployment Readiness V1

This project is deployable as a mock MVP for demos and internal review. It is not a production CS2 rendering service, not a production auth system, and not a durable media storage architecture.

## Runtime Configuration

### Backend API and Frontend

| Variable | Default | Used by | Notes |
| --- | --- | --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | Frontend | Public browser-facing API origin used by `frontend/lib/api.ts` and media URL resolution. For Compose on the host, keep this as `http://localhost:8000`. |
| `BACKEND_PUBLIC_URL` | `http://localhost:8000` | Backend API, worker | Public API origin reported by health/readiness output. |
| `MEDIA_URL_BASE` | unset | Backend API, worker | Optional public base for absolute `/media/videos/...` URLs. If unset, media URLs remain relative and the frontend resolves them against `NEXT_PUBLIC_API_BASE_URL`. |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Backend API | Comma-separated list of allowed frontend origins. Add deployed frontend origins here. |

### Backend Dependencies

| Variable | Default | Used by | Notes |
| --- | --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg2://cs2coach:cs2coach@localhost:5432/cs2coach` | API, worker | Use `postgres` as the host inside Docker Compose. Do not commit production credentials. |
| `REDIS_URL` | `redis://localhost:6379/0` | API, worker | Use `redis` as the host inside Docker Compose. |
| `REDIS_QUEUE_NAME` | `cs2-demo-jobs` | API, worker | Queue used for parse, mock render, and render clip job dispatch. |

### Local File Storage

| Variable | Default | Used by | Notes |
| --- | --- | --- | --- |
| `ARTIFACT_STORAGE_ROOT` | `/data` | API, worker | Base root for the local filesystem storage adapter. Per-category variables below override individual roots. |
| `REPLAY_STORAGE_DIR` | `/data/replays` | API, worker | Stores replay JSON blobs. |
| `DEMO_UPLOAD_STORAGE_DIR` | `/data/uploads` | API, worker | Stores uploaded `.dem` and `.zip` files. Treat every upload as untrusted input. |
| `VIDEO_STORAGE_DIR` | `/data/videos` | API, worker | Stores manual or render-worker MP4 outputs served under `/media/videos`. |
| `SUMMARY_STORAGE_DIR` | `/data/summaries` | API, worker | Reserved for compact generated summary artifacts. |

Local paths and Docker volumes are acceptable for the mock MVP and local demos. Production should replace the local adapter with object storage such as S3/R2 plus upload quarantine and lifecycle rules.

Artifact storage keys use stable application-level categories:

| Category | Key shape | Contents |
| --- | --- | --- |
| uploads | `local://uploads/{demo_id}/{safe_filename}` | Uploaded `.dem` or `.zip` source files. |
| replays | `local://replays/{demo_id}.json` | Replay contract JSON blobs. |
| summaries | `local://summaries/{demo_id}/{safe_filename}` | Reserved compact summary artifacts. |
| videos | `local://videos/{demo_id}/{safe_filename}` | Manual uploads and render-worker media outputs served under `/media/videos/...`. |

PostgreSQL should store compact metadata and storage keys only. Large `.dem`, replay JSON, raw parser dumps, and video files stay in artifact storage.

### Dev Owner Boundary

| Variable | Default | Used by | Notes |
| --- | --- | --- | --- |
| `DEV_USER_ID` | `dev-user` | Backend API | Default owner id for local requests. `X-Dev-User-Id` can override it in local tests. This is not production authentication. |

Production auth should replace the local helper with a real identity provider and keep mapping the authenticated subject to `owner_id`.

### Render Clip and Render Worker

| Variable | Default | Used by | Notes |
| --- | --- | --- | --- |
| `MAX_RENDER_CLIP_SECONDS` | `60` | API | Maximum accepted `render_clip` duration. |
| `RENDER_WORKER_TOKEN` | `dev-render-worker-token` | API, render-worker | Token expected in `X-Render-Worker-Token` for manifest, media upload, and callback endpoints. This is a local development gate, not production auth. |
| `API_BASE_URL` | `http://localhost:8000` | render-worker | API origin used by `render-worker/runner.py`. |
| `WORK_DIR` | `.render-worker-work` | render-worker | Local workspace for manifest snapshots and manual operator files. |
| `POLL_INTERVAL_SECONDS` | `5` | render-worker | Poll interval setting retained for worker loops and future adapters. |
| `DEV_FAKE_VIDEO_PATH` | unset | render-worker fake adapter | Optional local MP4 used to prove the manifest -> upload -> callback chain without real rendering. |
| `CS2_INSTALL_DIR` | unset | render-worker manual adapter | Optional path written into manual operator instructions only. The runner does not launch CS2. |
| `STEAM_USER_DATA_DIR` | unset | render-worker manual adapter | Optional path written into manual operator instructions only. |
| `CS2_MANUAL_OUTPUT_FILENAME` | `{job_id}.mp4` | render-worker manual adapter | Expected output filename template for manual operator completion. |

### Optional Sample Demo Smoke

| Variable / flag | Default | Used by | Notes |
| --- | --- | --- | --- |
| `SAMPLE_DEMO_PATH` | unset | `scripts/cloud_preview_smoke.py` | Absolute path to a local `.dem` or supported demo archive for fresh upload/parser smoke. When unset, sample upload is skipped unless required. |
| `SAMPLE_DEMO_NAME` | unset | `scripts/cloud_preview_smoke.py` | Optional display name applied to the uploaded sample demo through the normal demo update API. |
| `REQUIRE_SAMPLE_DEMO` / `SAMPLE_DEMO_REQUIRED` | `0` | `scripts/cloud_preview_smoke.py` | Treat missing or invalid `SAMPLE_DEMO_PATH` as a smoke failure. The CLI flag `--require-sample` does the same. |

Keep sample demos under ignored local paths such as `sample-demos/`, `samples/`, or `.local/samples/`, and set `SAMPLE_DEMO_PATH` to that file. Do not commit real `.dem`, demo archives, replay blobs, or media outputs. Real match demos can contain player data or licensed match content; use only samples you are allowed to store and upload to the target preview.

See `docs/sample_demo_fixture_v1.md` for the local convention and ad hoc upload command.

## Health Check

`GET /health` returns HTTP 200 with a compact readiness payload:

```json
{
  "status": "ok",
  "api": true,
  "database": true,
  "redis": true,
  "workerDependencies": {
    "redisQueueName": "cs2-demo-jobs",
    "redisQueueConfigured": true,
    "renderWorkerTokenConfigured": true,
    "maxRenderClipSeconds": 60
  },
  "publicUrls": {
    "backendPublicUrl": "http://localhost:8000",
    "mediaUrlBase": null,
    "effectiveMediaUrlBase": "http://localhost:8000"
  },
  "storage": {
    "artifactStorageRoot": "/data",
    "replayStorageDir": "/data/replays",
    "demoUploadStorageDir": "/data/uploads",
    "videoStorageDir": "/data/videos",
    "summaryStorageDir": "/data/summaries"
  }
}
```

`status` becomes `degraded` when PostgreSQL, Redis, or required worker configuration is unavailable. This endpoint confirms dependency reachability and configuration shape; it is not a monitoring system.

## Safe Diagnostics

`GET /diagnostics` returns a safer troubleshooting payload for previews and local smoke failures. It includes compact API readiness, DB/Redis/storage readiness, worker dependency readiness, Redis queue length, worker heartbeat age, recent job counts by type/status, recent failed job summaries, and an inferred render-worker status when recent `render_clip` jobs make that clear.

It must not expose secrets, full env dumps, local absolute storage paths, stack traces, raw parser data, upload contents, or replay/media payloads. Storage readiness is represented as category booleans; demo artifacts are represented as key-present/artifact-present booleans.

`GET /demos/{demo_id}/diagnostics` is owner-scoped through the same dev-only `X-Dev-User-Id` boundary as the rest of the mock app. It reports compact parse failure metadata, last parse/render job status, source/replay artifact presence, media URL availability, and map calibration/fallback state for one demo.

The backend Redis worker writes a simple heartbeat under a Redis key while polling and after job activity. This is only an internal freshness signal for the mock MVP; it is not a production lease, scheduler, or monitoring backend.

## Runtime Status Snapshots

Demo list/detail responses include compact ingestion state for upload and parser diagnosis:

```json
{
  "phase": "uploaded",
  "active": true,
  "stale": false,
  "retryable": false,
  "attemptCount": 0,
  "jobType": "real_parse",
  "jobStatus": "queued",
  "hasSourceDemo": true,
  "failure": null
}
```

Failed parser jobs include a short failure object with `errorCode`, `message`, `failedAt`, `updatedAt`, `retryable`, and `attemptCount`. This is intentionally compact and should not contain raw stack traces, sensitive local filesystem paths, raw parser dumps, or large event payloads.

Expected parser failure codes for the mock MVP are:

- `INVALID_DEMO`
- `UNSUPPORTED_PARSER_FORMAT`
- `MISSING_MATCH_METADATA`
- `MISSING_FRAMES`
- `NORMALIZATION_FAILED`
- `STORAGE_READ_FAILED`
- `PARSER_UNEXPECTED`

Missing optional parser event families should stay a partial parse success. The replay contract diagnostics report missing/degraded event families, and deterministic analyzer rules that depend on those families skip themselves rather than failing the parse.

If a failed demo still has a valid `source_storage_key`, retry parsing through the owner-scoped endpoint:

```bash
curl -X POST http://localhost:8000/demos/{demo_id}/parse/retry
```

Replay responses include compact contract diagnostics for QA:

```json
{
  "contractVersion": "replay_contract_v1",
  "normalizedLegacy": false,
  "parserEventCount": 12,
  "roundCount": 24,
  "playerCount": 10,
  "frameCount": 720,
  "missingFields": [],
  "degradedFields": [],
  "eventFamilyCounts": {
    "combat": 4,
    "damage": 2,
    "objective": 3,
    "utility": 3
  },
  "missingEventFamilies": []
}
```

These snapshots are deploy-readiness aids, not production observability. Keep detailed logs in process logs or a future logging backend, not in PostgreSQL or replay blobs.

Replay blobs should be normalized before storage: tick rates and tick ranges are repaired to safe defaults when possible, rounds/frames are sorted, unstable player IDs/names are stabilized, malformed parser events are ignored best-effort, and non-finite coordinates are dropped. Unknown maps must use explicit fallback metadata and dynamic bounds rather than Dust II radar assets or transforms.

## Local Verification

Run all local checks in the expected order:

```bash
./scripts/verify.sh
```

The script covers:

- backend compile
- backend unit tests
- render-worker compile
- render-worker unit tests
- frontend lint
- frontend typecheck
- frontend build

Frontend helper regressions are separate Node checks and should be run when dashboard, replay diagnostics, parser event presentation, round review, coaching review, or map-config helpers change:

```bash
cd frontend
node lib/demo-library.test.mjs
node lib/replay-diagnostics.test.mjs
node lib/replay-events.test.mjs
node lib/round-review.test.mjs
node lib/coaching-review.test.mjs
node lib/replay-quality-fixtures.test.mjs
node lib/map-config.test.mjs
```

Docker checks still run separately:

```bash
docker compose build
docker compose up -d
curl http://localhost:8000/health
curl http://localhost:8000/diagnostics
```

Cloud preview smoke is documented in `docs/cloud_preview_deploy_v1.md` and can be run against a local or hosted preview:

```bash
API_BASE_URL=http://localhost:8000 FRONTEND_URL=http://localhost:3000 python3 scripts/cloud_preview_smoke.py
```

Without `SAMPLE_DEMO_PATH`, the smoke still runs health, frontend, mock upload, replay/coaching, render job, media-route checks, and a compact diagnostics summary, then exits successfully with a sample-skip message. If any smoke step fails, it attempts to fetch `/diagnostics` and prints a compact summary; if that endpoint is unavailable, the original failure remains visible. With a configured sample, it uploads through `POST /uploads/demo`, waits for parse completion, and prints map, round, coaching, and map calibration/fallback status:

```bash
SAMPLE_DEMO_PATH=/absolute/path/to/sample.dem python3 scripts/cloud_preview_smoke.py
SAMPLE_DEMO_PATH=/absolute/path/to/sample.dem python3 scripts/cloud_preview_smoke.py --require-sample
```

Use `--require-sample` or `REQUIRE_SAMPLE_DEMO=1` when preview validation must prove fresh real-demo ingestion. Existing parsed database rows are not enough for that check because they do not exercise upload storage, Redis job dispatch, parser execution, replay storage, or analyzer completion for a new sample.

## Docker Notes

- Compose service names are used for in-container dependencies: `postgres` and `redis`.
- `postgres` and `redis` have health checks.
- `api` waits for healthy PostgreSQL and Redis, exposes `/health`, and has a Compose health check.
- `frontend` waits for the API service health check before starting.
- The default Compose frontend still uses `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` because browser requests originate from the host browser, not from the container network.
- `docker-compose.preview.yml` switches the frontend to a production Next.js build via `frontend/Dockerfile.preview`.
- Rebuild the preview frontend image whenever `NEXT_PUBLIC_API_BASE_URL` changes because it is bundled at build time.
- Set `MEDIA_URL_BASE` to the public backend origin when the frontend and backend are served from different hosts and absolute media URLs are preferred.
- The local storage adapter writes uploads, replay blobs, summaries, and videos under `/data` by default. Keep the adapter boundary when replacing local storage with S3/R2 later.

## Deployable Boundaries

Ready for mock MVP deployment:

- Demo Library upload, search, status/map filtering, sorting, rename, and soft archive flows.
- Upload/parser ingestion snapshots, failed parse metadata, stale/active indicators, and owner-scoped retry from stored source artifacts.
- Mock upload and real `.dem`/`.zip` upload into local or mounted storage.
- Storage-key backed uploads, replay blobs, and media URLs through the local storage adapter.
- Async parse queue using Redis plus backend worker.
- Replay contract JSON blobs with backward-compatible normalization, compact parser events, contract diagnostics, and deterministic rules-based coaching rows.
- Demo detail review: round navigation, timeline markers, tactical map sync, replay diagnostics, degraded states, coaching cards, manual video calibration, and render job status UI.
- Render Worker V1 manifest claim, media upload, and terminal-safe result callback contract.
- Compact backend/frontend parser quality fixtures for regression coverage.

Still mock/dev-only:

- `DEV_USER_ID` and `X-Dev-User-Id` are local owner scoping only, not production auth.
- `render-worker` fake and manual adapters are not real GPU rendering.
- Local filesystem and Docker volumes are not final production object storage.
- `.dem` and archive uploads are untrusted inputs and need stronger production quarantine and scanning.
- Real first-person CS2 rendering still belongs in an external controlled Windows/Linux GPU worker. API and worker containers must not run Steam, CS2, OBS, or ffmpeg automation.
- Manual MP4 upload/calibration is a development and QA bridge, not the primary product path.

## Deploy Smoke Checklist

1. Build and start the stack:

   ```bash
   docker compose up --build
   ```

2. Confirm health:

   ```bash
   curl http://localhost:8000/health
   ```

3. Open `http://localhost:3000/dashboard`.
4. Create a mock upload and wait for it to complete.
5. If a sample is available, set `SAMPLE_DEMO_PATH` and upload a real `.dem` or `.zip` containing a `.dem`.
6. Open a demo detail page.
7. Use round review quick jumps and confirm first-person shell/video, tactical map, timeline, parser markers, and coaching cards stay synchronized.
8. Confirm Replay Contract diagnostics show counts and no unexpected degraded fields for a healthy mock demo.
9. For a failed parse fixture or seeded row, confirm the Dashboard shows failure metadata and retry availability only when a source artifact exists.
10. Click `Generate Clip` on a coaching event.
11. Confirm render job status appears in the UI and `/demos/{demo_id}/render/jobs`.
12. For render-worker callback validation, run either the fake adapter with `DEV_FAKE_VIDEO_PATH` or the manual adapter flow documented in `render-worker/README.md`.
13. For hosted preview validation, run `python3 scripts/cloud_preview_smoke.py` with `API_BASE_URL` and `FRONTEND_URL` set to the public origins. Add `SAMPLE_DEMO_PATH` and `--require-sample` for strict parser-ingestion validation.
