# Deployment Readiness V1

This project includes the Stage 3 provider-neutral private artifact store and safe `.dem` intake boundary plus the Steam-first Phase 1 account foundation. It is still not a match-history sync/download service, reliable/crash-recoverable job system, isolated parser runtime, production observability/backup platform, or real CS2 rendering service.

The Steam/account contract is in `docs/steam_auth_accounts_v1.md`; the shared owner/private-media acceptance matrix is in `docs/production_auth_owner_private_media_v1.md`. For a repeatable development preview handoff, use `docs/internal_preview_packaging_v1.md`. This document remains the runtime configuration and readiness reference.

## Runtime Configuration

### Backend API and Frontend

| Variable | Default | Used by | Notes |
| --- | --- | --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | Frontend | Public browser-facing API origin used by the credentialed API client and private media URL resolution. It is not a secret. |
| `NEXT_PUBLIC_AUTH_PROVIDER` | `steam` | Frontend | Public login selector; must match backend `AUTH_PROVIDER`. It contains no credential. |
| `FRONTEND_PUBLIC_URL` | `http://localhost:3000` | Backend API | Trusted post-login frontend redirect origin. Production requires the exact same HTTPS origin as `BACKEND_PUBLIC_URL` and the sole value in `CORS_ORIGINS`. |
| `BACKEND_PUBLIC_URL` | `http://localhost:8000` | Backend API, worker | Public API origin. Production requires an HTTPS origin with no path/query/fragment; `/health` does not echo it. |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Backend API | Credentialed browser origins. Production requires exactly one HTTPS origin equal to `FRONTEND_PUBLIC_URL`; wildcard, stale, and sibling origins are rejected. |

### Backend Dependencies

| Variable | Default | Used by | Notes |
| --- | --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg2://cs2coach:cs2coach@localhost:5432/cs2coach` | API, worker | Use `postgres` as the host inside Docker Compose. Do not commit production credentials. |
| `REDIS_URL` | `redis://localhost:6379/0` | API, worker | Use `redis` as the host inside Docker Compose. |
| `REDIS_QUEUE_NAME` | `cs2-demo-jobs` | API, worker | Queue used for parse, mock render, and render clip job dispatch. |

### Private Artifact Storage

| Variable | Default | Used by | Notes |
| --- | --- | --- | --- |
| `ARTIFACT_STORAGE_BACKEND` | `local` | API, worker | `local` is development/test only. Production startup requires `s3`. |
| `OBJECT_STORAGE_BUCKET` | unset | API, worker | Required private S3-compatible bucket in production. It is never returned to clients. |
| `OBJECT_STORAGE_PREFIX` | `cs2-artifacts-v1` | API, worker | Bounded private namespace; logical references remain backend-neutral. |
| `OBJECT_STORAGE_REGION` | `us-east-1` | API, worker | S3-compatible region setting; not a cloud-vendor selection. |
| `OBJECT_STORAGE_ENDPOINT_URL` | unset | API, worker | Optional S3-compatible endpoint. Production accepts HTTPS only. |
| `OBJECT_STORAGE_ACCESS_KEY_ID` | unset | API, worker | Optional server-side credential when no runtime credential chain is available. |
| `OBJECT_STORAGE_SECRET_ACCESS_KEY` | unset | API, worker | Must be configured as a pair with the access key and never exposed to the browser. |
| `ARTIFACT_QUARANTINE_TTL_SECONDS` | `3600` | API, maintenance | Deterministic cutoff for abandoned private quarantine cleanup. |
| `MAX_DEMO_UPLOAD_BYTES` | `1073741824` | API, worker | Authoritative actual source-byte limit. |
| `MAX_VIDEO_UPLOAD_BYTES` | `2147483648` | API, render-worker API | Authoritative actual video-byte limit for dev/QA/worker paths. |
| `MAX_REPLAY_ARTIFACT_BYTES` | `134217728` | API, worker | Bound for replay JSON writes and reads. |
| `UPLOAD_CHUNK_BYTES` | `1048576` | API, worker | Bounded streaming chunk; validation never loads a full large artifact into memory. |

The provider-neutral boundary generates logical references that bind state, kind, owner, demo, and a server-generated artifact ID. It supports private streamed writes, exact size/SHA-256 metadata, conditional reads, HEAD, delete, promotion, range delivery, deterministic quarantine cleanup, and bounded source materialization. Production objects have no public ACL/URL and browser media continues to use only the owner-scoped route. The full contract is `docs/object_storage_safe_artifact_intake_v1.md`.

### Local Adapter Compatibility

| Variable | Default | Used by | Notes |
| --- | --- | --- | --- |
| `ARTIFACT_STORAGE_ROOT` | `/data` | API, worker | Base root for the local filesystem storage adapter. Per-category variables below override individual roots. |
| `REPLAY_STORAGE_DIR` | `/data/replays` | API, worker | Stores replay JSON blobs. |
| `DEMO_UPLOAD_STORAGE_DIR` | `/data/uploads` | API, worker | Stores uploaded `.dem` files. Archive support may exist for development compatibility, but the product UI should present `.dem` upload as the real path. Treat every upload as untrusted input. |
| `VIDEO_STORAGE_DIR` | `/data/videos` | API, worker | Stores manual or render-worker MP4 outputs. Browser bytes are delivered only through owner-scoped `/demos/{demo_id}/media/video`. |
| `SUMMARY_STORAGE_DIR` | `/data/summaries` | API, worker | Reserved for compact generated summary artifacts. |

Local paths and Docker volumes remain a development/test adapter and legacy-read compatibility surface. They are rejected in production. New accepted source, replay, and video records use backend-neutral logical references; a legacy local key alone is never proof that a new source passed intake.

Legacy storage keys use these local-only categories:

| Category | Key shape | Contents |
| --- | --- | --- |
| uploads | `local://uploads/{demo_id}/{safe_filename}` | Uploaded `.dem` source files; archive inputs are development compatibility only. |
| replays | `local://replays/{demo_id}.json` | Replay contract JSON blobs. |
| summaries | `local://summaries/{demo_id}/{safe_filename}` | Reserved compact summary artifacts. |
| videos | `local://videos/{demo_id}/{safe_filename}` | Manual uploads and render-worker outputs. The key is internal and is projected to the private demo media route for users. |

PostgreSQL should store compact metadata and storage keys only. Large `.dem`, replay JSON, raw parser dumps, and video files stay in artifact storage.

### Production Identity and Browser Session

| Variable | Default | Used by | Notes |
| --- | --- | --- | --- |
| `AUTH_MODE` | unset | Backend API | Required explicit value: `development`, `test`, or `production`. Unsupported or missing values fail runtime validation. |
| `AUTH_PROVIDER` | unset | Backend API | Required explicitly in production: `steam`, or `oidc` for the existing compatibility flow. Local Compose selects `steam`. |
| `STEAM_AUTH_STATE_COOKIE_NAME` | `__Host-cs2_steam_state` | Backend API | Single-use Steam state cookie; production requires a distinct `__Host-` name. |
| `STEAM_OPENID_NONCE_TTL_SECONDS` | `600` | Backend API, Redis | Steam assertion freshness and replay-reservation window; must cover login TTL plus skew. |
| `STEAM_WEB_API_KEY` | unset | Backend API | Optional server-only GetPlayerSummaries key for display metadata. It is not needed for SteamID64 authentication. |
| `OIDC_ISSUER` | unset | Backend API | Exact HTTPS production issuer expected in verified identity tokens. |
| `OIDC_CLIENT_ID` | unset | Backend API | OIDC client identifier and required audience. |
| `OIDC_CLIENT_SECRET` | unset | Backend API | Optional server-side secret for confidential clients. Never expose it to the frontend. |
| `OIDC_ALLOWED_ALGORITHMS` | `RS256,ES256` | Backend API | Non-empty subset of the supported asymmetric algorithms. Symmetric and `none` algorithms are rejected. |
| `OIDC_AUTHORIZATION_ENDPOINT` | unset | Backend API | HTTPS Authorization Code + PKCE endpoint. |
| `OIDC_TOKEN_ENDPOINT` | unset | Backend API | HTTPS server-side code exchange endpoint. |
| `OIDC_JWKS_URL` | unset | Backend API | HTTPS trusted asymmetric signing-key set. |
| `OIDC_REDIRECT_URI` | unset | Backend API | Exact registered HTTPS backend `/auth/oidc/callback` URL, distinct from the frontend `/auth/callback` page. |
| `AUTH_COOKIE_SECURE` | `false` | Backend API | Must be enabled in production. |
| `AUTH_SESSION_COOKIE_NAME` | `__Host-cs2_session` | Backend API | Opaque `HttpOnly` browser session cookie; production requires the `__Host-` prefix. |
| `AUTH_STATE_COOKIE_NAME` | `__Host-cs2_oidc_state` | Backend API | Compatibility OIDC state cookie, used only when `AUTH_PROVIDER=oidc`. |
| `AUTH_SESSION_TTL_SECONDS` | `3600` | Backend API, Redis | Session TTL; production accepts `1..86400` and caps it at identity-token expiry. |
| `AUTH_LOGIN_TTL_SECONDS` | `300` | Backend API, Redis | One-time state/nonce/PKCE attempt TTL; production accepts `1..600`. |
| `AUTH_CLOCK_SKEW_SECONDS` | `30` | Backend API | Bounded identity timestamp leeway; production accepts `0..300`. |
| `DEV_USER_ID` | `dev-user` | Backend API | Development/test owner harness only. `X-Dev-User-Id` is never a production identity source. |

Production API startup fails closed unless the selected provider, secure `__Host-` cookie, exact single HTTPS application origin, exact single-origin CORS, bounded session/nonce settings, and non-default render-worker credential are valid. Steam realm and callback are derived from `BACKEND_PUBLIC_URL`; complete OIDC configuration is required only when `AUTH_PROVIDER=oidc`. The queue worker uses a narrower validation path and does not receive browser Steam/OIDC secrets. Verified identities resolve through `accounts` and `external_identities` to a stable opaque `owner_v1_...`; browser sessions remain random opaque Redis entries. Unsafe browser mutations require the exact `FRONTEND_PUBLIC_URL` origin. No SteamID64, provider token, raw identity claim, auth secret, or owner ID belongs in a frontend-visible payload.

Every browser-private auth, demo, upload, replay, coaching, diagnostics, render-job, and media response—including `4xx` failures—sets `Cache-Control: private, no-store` and merges `Cookie, Origin` into `Vary`. Render failures persist and expose only the stable `RENDER_FAILED` or `RENDER_WORKER_UNAVAILABLE` code and safe message; callback-provided error text and background exception strings do not enter the database, replay payload, user JSON, or logs.

### Render Clip and Render Worker

| Variable | Default | Used by | Notes |
| --- | --- | --- | --- |
| `MAX_RENDER_CLIP_SECONDS` | `60` | API | Maximum accepted `render_clip` duration. |
| `RENDER_WORKER_TOKEN` | `dev-render-worker-token` | API, render-worker | Separate service credential for manifest, media upload, and callback endpoints. Production rejects the development default. It is not a browser identity credential. |
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
| `SAMPLE_DEMO_PATH` | unset | `scripts/cloud_preview_smoke.py` | Absolute path to a local `.dem` for fresh upload/parser smoke. When unset, sample upload is skipped unless required. |
| `SAMPLE_DEMO_NAME` | unset | `scripts/cloud_preview_smoke.py` | Optional display name applied to the uploaded sample demo through the normal demo update API. |
| `REQUIRE_SAMPLE_DEMO` / `SAMPLE_DEMO_REQUIRED` | `0` | `scripts/cloud_preview_smoke.py` | Treat missing or invalid `SAMPLE_DEMO_PATH` as a smoke failure. The CLI flag `--require-sample` does the same. |

Keep sample demos under ignored local paths such as `sample-demos/`, `samples/`, or `.local/samples/`, and set `SAMPLE_DEMO_PATH` to that file. Do not commit real `.dem`, demo archives, replay blobs, or media outputs. Real match demos can contain player data or licensed match content; use only samples you are allowed to store and upload to the target preview.

See `docs/sample_demo_fixture_v1.md` for the local convention and ad hoc upload command.

## Health Check

`GET /health` remains public and returns only coarse readiness:

```json
{
  "status": "ok"
}
```

`status` becomes `degraded` when PostgreSQL, Redis, or required worker configuration is unavailable. The response does not reveal dependency names, internal URLs, credentials, queue names, or storage paths. It is not a monitoring system.

## Safe Diagnostics

In `development` and `test`, `GET /diagnostics` returns a compact troubleshooting payload for preview and smoke failures. It includes API readiness, DB/Redis/storage readiness, worker dependency readiness, Redis queue length, worker heartbeat age, recent job counts by type/status, recent failed job summaries, and an inferred render-worker status when recent `render_clip` jobs make that clear. Production returns `404`; deployment operators must use a separately protected operational channel in a later stage.

It must not expose secrets, full env dumps, local absolute storage paths, stack traces, raw parser data, upload contents, or replay/media payloads. Storage readiness is represented as category booleans; demo artifacts are represented as key-present/artifact-present booleans.

`GET /demos/{demo_id}/diagnostics` is authenticated and owner-scoped through the same session-derived owner as every user endpoint. It reports compact parse failure metadata, last parse/render job status, source/replay artifact presence, private media availability, and map calibration/fallback state for one demo.

The backend Redis worker writes a simple heartbeat under a Redis key while polling and after job activity. This is only an internal freshness signal for the mock MVP; it is not a production lease, scheduler, or monitoring backend.

## Private Video Readiness

User-facing replay and video status payloads are rebuilt from explicit public-field allowlists and expose only `/demos/{demo_id}/media/video`; they omit internal `storageKey`, unknown internal fields, and local path details. Replay/source storage references are bound to the current demo, and replay payload `demoId` must match its database demo. The API does not mount `/media/videos` as static content. Local private media rejects symlinked path components and streams from the validated file descriptor so a later path replacement cannot redirect the response.

Before deployment, verify authenticated `GET`, `HEAD`, and byte-range requests against a ready owner video. A valid owner should receive `200` or `206`; an unsatisfiable range should receive `416`; anonymous/expired/revoked sessions should receive `401`; another owner, a guessed ID, missing media, cross-demo storage metadata, traversal, or a symlink escape should receive the same generic `404` without bytes or path details. Confirm `Cache-Control: private, no-store`, safe MP4 MIME, `Accept-Ranges: bytes`, and that the frontend retains the 2D fallback when media is unavailable.

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

Stage 2 and Stage 3 focused gates also run directly:

```bash
PYTHONPATH=backend python3 -m unittest \
  backend.tests.test_artifact_storage_config \
  backend.tests.test_artifact_store_contract \
  backend.tests.test_artifact_intake \
  backend.tests.test_request_limits \
  backend.tests.test_production_auth \
  backend.tests.test_private_media \
  backend.tests.test_auth_owner_boundary \
  backend.tests.test_diagnostics
cd frontend
node lib/auth.test.mjs
node lib/private-media.test.mjs
```

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
# development/test only:
curl http://localhost:8000/diagnostics
```

Release-candidate non-browser checks can be run together:

```bash
./scripts/rc_check.sh
```

The RC helper wraps `./scripts/verify.sh`, Docker build/up, health, development diagnostics, no-sample cloud preview smoke, and sample smoke when `SAMPLE_DEMO_PATH` is set. Use `REQUIRE_SAMPLE_DEMO=1` when a missing sample must fail the gate. It is a development harness and does not replace production Steam/account/owner/private-media validation or manual browser QA; use `docs/release_candidate_qa_v1.md` for the full checklist.

For packaging evidence and reviewer handoff, follow `docs/internal_preview_packaging_v1.md` after the RC helper finishes.

Cloud preview smoke is documented in `docs/cloud_preview_deploy_v1.md` and can be run against a local or hosted preview:

```bash
API_BASE_URL=http://localhost:8000 FRONTEND_URL=http://localhost:3000 python3 scripts/cloud_preview_smoke.py
```

The current script smoke is a development/test harness. Without `SAMPLE_DEMO_PATH`, it runs health, frontend, mock upload, replay/coaching, render job, private-media routing checks, and a compact development diagnostics summary, then exits successfully with a sample-skip message. It does not replace the production provider/account/owner matrix. With a configured sample, it uploads through `POST /uploads/demo`, waits for parse completion, and prints map, round, coaching, and map calibration/fallback status:

```bash
SAMPLE_DEMO_PATH=/absolute/path/to/sample.dem python3 scripts/cloud_preview_smoke.py
SAMPLE_DEMO_PATH=/absolute/path/to/sample.dem python3 scripts/cloud_preview_smoke.py --require-sample
```

Use `--require-sample` or `REQUIRE_SAMPLE_DEMO=1` when preview validation must prove fresh real-demo ingestion. Existing parsed database rows are not enough for that check because they do not exercise upload storage, Redis job dispatch, parser execution, replay storage, or analyzer completion for a new sample.

Manual first-run preview should start at `/dashboard`. Verify the empty/loading/fetch-failed/no-result states expose clear actions, create a mock demo for the fast synthetic path, upload a real `.dem` for parser ingestion when a sample exists, and use the post-upload/open-demo link to inspect detail status. On detail, check the compact summary strip for file, map, calibration/fallback, parser status or failure code, media status, and latest render job status before testing replay controls.

## Docker Notes

- Compose service names are used for in-container dependencies: `postgres` and `redis`.
- `postgres` and `redis` have health checks.
- `api` waits for healthy PostgreSQL and Redis, exposes `/health`, and has a Compose health check.
- `frontend` waits for the API service health check before starting.
- The default Compose frontend still uses `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` because browser requests originate from the host browser, not from the container network.
- `docker-compose.preview.yml` switches the frontend to a production Next.js build via `frontend/Dockerfile.preview`.
- Rebuild the preview frontend image whenever `NEXT_PUBLIC_API_BASE_URL` or `NEXT_PUBLIC_AUTH_PROVIDER` changes because both are bundled at build time.
- Private media always uses the API's owner-scoped `/demos/{demo_id}/media/video` route. Production frontend pages and API/auth/media paths must share one exact HTTPS origin so `__Host-` cookies and CSRF checks protect both API and native video requests.
- The local artifact adapter writes private logical-reference objects under `/data` by default and remains development/test only. Production startup requires the private S3-compatible adapter and never exposes a bucket URL.

## Deployable Boundaries

Ready at the Stage 3 application boundary:

- Steam OpenID 2.0 direct verification, formal account/external-identity mapping, Redis opaque browser sessions, logout revocation, frontend session-expiry handling, and an explicit OIDC compatibility provider.
- Owner-scoped private video GET/HEAD/Range delivery without a public static media mount.
- Demo Library upload, search, status/map filtering, sorting, rename, and soft archive flows.
- Upload/parser ingestion snapshots, failed parse metadata, stale/active indicators, and owner-scoped retry from stored source artifacts.
- Mock upload plus public `.dem` intake through private quarantine, bounded streaming, SHA-256 metadata, verified promotion, accepted source binding, and parser dispatch gating.
- Provider-neutral private source/replay/video operations through local development/test and S3-compatible production adapters; logical references bind owner, demo, kind, and lifecycle state.
- Deterministic rejected/incomplete/quarantine cleanup and bounded, context-managed source materialization for the current parser compatibility layer.
- Async parse queue using Redis plus backend worker.
- Replay contract JSON blobs with backward-compatible normalization, compact parser events, contract diagnostics, and deterministic rules-based coaching rows.
- Demo detail review: round navigation, timeline markers, tactical map sync, replay diagnostics, degraded states, coaching cards, manual video calibration, and render job status UI.
- Render Worker V1 manifest claim, media upload, and terminal-safe result callback contract.
- Compact backend/frontend parser quality fixtures for regression coverage.

Remaining staged gaps:

- `DEV_USER_ID` and `X-Dev-User-Id` remain only as an explicit development/test harness and are not accepted in production.
- `render-worker` fake and manual adapters are not real GPU rendering.
- Object bucket/IAM/resource provisioning remains an external deployment decision; Stage 3 creates no cloud resources or real secrets.
- Stage 3 byte-level intake cannot prove semantic `.dem` validity. The current parser still runs after accepted promotion; isolation and CPU/memory/disk/time limits remain Stage 5.
- Redis dispatch still lacks durable delivery, crash recovery, atomic claim, redelivery, and idempotent execution. Those remain Stage 4 rather than being hidden inside artifact promotion.
- Account/external-identity DDL now uses a tracked forward-only migration; legacy schema backfills, production observability, CI/CD, and backup/restore remain broader Stage 6 work.
- Real first-person CS2 rendering still belongs in an external controlled Windows/Linux GPU worker. API and worker containers must not run Steam, CS2, OBS, or ffmpeg automation.
- Manual MP4 upload/calibration is a development and QA bridge, not the primary product path.

## Deploy Smoke Checklist

For release-candidate sign-off, use `docs/release_candidate_qa_v1.md`. The shorter deploy smoke checklist remains:

1. Build and start the stack:

   ```bash
   docker compose up --build
   ```

2. Confirm health:

   ```bash
   curl http://localhost:8000/health
   ```

3. Open `http://localhost:3000/dashboard`. Development mode should use the explicit local harness; a production candidate must redirect an anonymous browser through Steam OpenID (or the explicitly selected OIDC compatibility flow) and return through the frontend callback without assertion/session tokens in the URL.
4. Create a mock upload and wait for it to complete.
5. If a sample is available, set `SAMPLE_DEMO_PATH` and upload a real `.dem`.
6. Open a demo detail page.
7. Use round review quick jumps and confirm first-person shell/private video, tactical map, timeline, parser markers, and coaching cards stay synchronized.
8. Confirm Replay Contract diagnostics show counts and no unexpected degraded fields for a healthy mock demo.
9. For a failed parse fixture or seeded row, confirm the Dashboard and detail summary show compact failure metadata such as `INVALID_DEMO` or `UNSUPPORTED_PARSER_FORMAT`, and retry availability only when a source artifact exists.
10. Click `Generate Clip` on a coaching event.
11. Confirm render job status appears in the UI and `/demos/{demo_id}/render/jobs`.
12. For render-worker callback validation, run either the fake adapter with `DEV_FAKE_VIDEO_PATH` or the manual adapter flow documented in `render-worker/README.md`.
13. Validate owner A, owner B, anonymous, expired, and revoked sessions across every surface in `docs/production_auth_owner_private_media_v1.md`, including private video GET/HEAD/Range and copied/guessed URL denial.
14. Confirm production `/diagnostics` is `404`, public `/health` is coarse, and `/media/videos/...` is not mounted.
15. For development-mode hosted preview validation, run `python3 scripts/cloud_preview_smoke.py` with `API_BASE_URL` and `FRONTEND_URL` set to the public origins. Add `SAMPLE_DEMO_PATH` and `--require-sample` for strict parser-ingestion validation.
