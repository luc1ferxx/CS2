# Deployment Readiness V1

This project is deployable as a mock MVP for demos and internal review. It is not a production CS2 rendering service, not a production auth system, and not a durable media storage architecture.

## Runtime Configuration

### Backend API and Frontend

| Variable | Default | Used by | Notes |
| --- | --- | --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | Frontend | Public browser-facing API origin used by `frontend/lib/api.ts` and media URL resolution. For Compose on the host, keep this as `http://localhost:8000`. |
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

Docker checks still run separately:

```bash
docker compose build
docker compose up -d
curl http://localhost:8000/health
```

## Docker Notes

- Compose service names are used for in-container dependencies: `postgres` and `redis`.
- `postgres` and `redis` have health checks.
- `api` waits for healthy PostgreSQL and Redis, exposes `/health`, and has a Compose health check.
- `frontend` waits for the API service health check before starting.
- The default Compose frontend still uses `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` because browser requests originate from the host browser, not from the container network.
- The local storage adapter writes uploads, replay blobs, summaries, and videos under `/data` by default. Keep the adapter boundary when replacing local storage with S3/R2 later.

## Deployable Boundaries

Ready for mock MVP deployment:

- Demo Library upload, search, status/map filtering, sorting, rename, and soft archive flows.
- Mock upload and real `.dem`/`.zip` upload into local or mounted storage.
- Storage-key backed uploads, replay blobs, and media URLs through the local storage adapter.
- Async parse queue using Redis plus backend worker.
- Replay contract JSON blobs and deterministic rules-based coaching rows.
- Demo detail review: round navigation, timeline markers, tactical map sync, coaching cards, manual video calibration, and render job status UI.
- Render Worker V1 manifest, media upload, and result callback contract.

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
5. If a sample is available, upload a real `.dem` or `.zip` containing a `.dem`.
6. Open a demo detail page.
7. Use round review quick jumps and confirm first-person shell/video, tactical map, timeline, parser markers, and coaching cards stay synchronized.
8. Click `Generate Clip` on a coaching event.
9. Confirm render job status appears in the UI and `/demos/{demo_id}/render/jobs`.
10. For render-worker callback validation, run either the fake adapter with `DEV_FAKE_VIDEO_PATH` or the manual adapter flow documented in `render-worker/README.md`.
