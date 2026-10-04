# Deployment Readiness V1

This project includes the Stage 3 provider-neutral private artifact store and safe `.dem` intake boundary plus the Steam-first account foundation, official sharing-code match discovery, and a fail-closed Demo import adapter. No licensed automatic Demo source is registered, so the shipped provider is disabled and manual `.dem` upload remains the supported path. It is still not a public Valve Demo download service, reliable/crash-recoverable job system, isolated parser runtime, production observability/backup platform, or real CS2 rendering service.

The Steam/account contract is in `docs/steam_auth_accounts_v1.md`; match authorization/encryption/sync is in `docs/steam_match_sync_v1.md`; provider/import/download policy is in `docs/steam_demo_import_v1.md`; the shared owner/private-media acceptance matrix is in `docs/production_auth_owner_private_media_v1.md`. For a repeatable development preview handoff, use `docs/internal_preview_packaging_v1.md`. This document remains the runtime configuration and readiness reference.

## Runtime Configuration

### Backend API and Frontend

| Variable | Default | Used by | Notes |
| --- | --- | --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | Frontend | Public browser-facing API origin used by the credentialed API client and private media URL resolution. It is not a secret. |
| `NEXT_PUBLIC_AUTH_PROVIDER` | `steam` | Frontend | Public login selector; must match backend `AUTH_PROVIDER`. It contains no credential. |
| `NEXT_PUBLIC_PRIVACY_CONTACT` | unset | Frontend (build time) | Contact shown on the public `/privacy` page for privacy questions and removal requests: an email becomes a `mailto:` link, an `http(s)://` URL becomes a link, anything else is plain text. Required for the VPS production deploy (`deploy.sh` refuses to run without it); unset (local/preview builds) the page says there is no public contact. Rebuild the frontend after changing it. |
| `NEXT_PUBLIC_DATA_REGION` | unset | Frontend (build time) | Server region shown on `/privacy` (for example `日本东京`). Unset shows "海外 VPS，具体地区由站长部署时选定". Rebuild the frontend after changing it. |
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
| `UPLOAD_STAGING_ROOT` | `/data/upload-staging` | API, worker | Chunked upload sessions stage their parts here, on the API host's local disk in every mode (not the artifact store); Compose mounts the named volume `upload-staging` into both containers so the worker's hourly sweep reaches it. Absolute path required in production. |
| `UPLOAD_PART_BYTES`, `UPLOAD_MAX_PARALLEL_PARTS`, `UPLOAD_PART_POOL` | `8388608`, `4`, `8` | API | Part size (4-32 MiB, whole MiB), parallel parts per session (1-6), and part bodies read at once process-wide (part memory is at most pool x part size, capped at 256 MiB). |
| `UPLOAD_SESSION_TTL_SECONDS`, `UPLOAD_SESSION_GLOBAL_LIMIT`, `UPLOAD_STAGING_MIN_FREE_BYTES` | `86400`, `6`, `5368709120` | API | Hard session lifetime from creation; open sessions across all owners (`503 upload_capacity_busy`); free space a new session needs beyond its size (`503 upload_storage_full`). Resource limits, enforced in every mode. |
| `DEMO_UPLOAD_DAILY_LIMIT` | `10` | API | Production only: new demos per owner in a rolling 24h window, counted from the content-free `upload_ledger` (one row per upload or Steam import, pruned after 24h), so archived and permanently deleted demos still count; over it `POST /uploads/demo` returns `429` `upload_daily_limit` with `Retry-After`. Range `0..1000` (checked in every mode); `0` disables it. |
| `DEMO_ACTIVE_PARSE_LIMIT` | `2` | API | Production only: demos per owner still `queued`/`parsing`/`analyzing`; uploads and parse retries over it return `429` `active_parse_limit` (`Retry-After: 60`). Range `0..100`; `0` disables it. |
| `PARSE_QUEUE_GLOBAL_LIMIT` | `50` | API | Production only: in-flight demos across all owners (database count, not Redis queue length); uploads and parse retries over it return `503` `parse_queue_full` (`Retry-After: 60`). Range `0..100000`; `0` disables it. |

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
| `STEAM_WEB_API_KEY` | unset | Backend API | Server-only GetPlayerSummaries/match-history publisher key; required in production, never frontend/worker-visible. |
| `STEAM_LOGIN_ALLOWLIST` | unset | Backend API | Invite gate: comma-separated individual Steam ID64s (at most 1000, unique), or `*` for every Steam account. Required in production with `AUTH_PROVIDER=steam`; with `AUTH_PROVIDER=oidc` only empty or `*` is accepted. An uninvited callback redirects to `/auth/callback?error=not_invited` before any account is created, and removing an ID ends that user's live sessions. Development/test validate the format but never enforce it. |
| `STEAM_CREDENTIAL_ENCRYPTION_KEY` | unset | Backend API | URL-safe base64 of 32 random bytes for AES-256-GCM; required and non-development in production. |
| `STEAM_CREDENTIAL_ENCRYPTION_KEY_VERSION` | `dev-v1` | Backend API | Active bounded key version stored beside ciphertext; production should set its own version. |
| `STEAM_SYNC_MAX_MATCHES` | `20` | Backend API | Hard maximum sharing codes consumed by one manual sync. |
| `STEAM_SYNC_TIMEOUT_SECONDS` | `5` | Backend API | Bounded Valve request timeout. |
| `STEAM_SYNC_RETRY_BASE_SECONDS`, `STEAM_SYNC_RETRY_MAX_SECONDS` | `30`, `3600` | Backend API | Persisted exponential backoff bounds. |
| `STEAM_SCHEDULED_SYNC_ENABLED` | `false` | Backend API | Independent V1 gate; true fails closed because no scheduler is installed. |
| `STEAM_DEMO_PROVIDER` | `disabled` | Backend API | This build registers no licensed source and rejects every other value in every runtime mode. |
| `STEAM_DEMO_EXPERIMENTAL_REPLAY_CDN_ENABLED` | `false` | Backend API | Unsupported community/undocumented replay path gate; true always fails startup. |
| `STEAM_DEMO_DOWNLOAD_ALLOWED_HOSTS` | unset | Backend API | Reserved exact-host allowlist for a separately reviewed licensed provider; setting hosts alone cannot enable downloads. |
| `STEAM_DEMO_DOWNLOAD_MAX_BYTES`, `STEAM_DEMO_DOWNLOAD_MAX_REDIRECTS` | `536870912`, `3` | Backend API | Raw uncompressed `.dem` transfer and independently revalidated redirect bounds. |
| `STEAM_DEMO_DOWNLOAD_CONNECT_TIMEOUT_SECONDS`, `STEAM_DEMO_DOWNLOAD_READ_TIMEOUT_SECONDS`, `STEAM_DEMO_DOWNLOAD_TOTAL_TIMEOUT_SECONDS` | `5`, `10`, `60` | Backend API | Bounded future licensed-provider network deadlines. |
| `STEAM_DEMO_DOWNLOAD_GLOBAL_CONCURRENCY`, `STEAM_DEMO_DOWNLOAD_OWNER_CONCURRENCY` | `4`, `1` | Backend API | Cross-process and owner-specific budgets; Redis stores only the expiring lease state. |
| `STEAM_DEMO_DOWNLOAD_CONCURRENCY_LEASE_SECONDS` | `120` | Backend API | Expiring Redis-backed concurrency claim; must cover the total timeout. |
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

Production API startup fails closed unless the selected identity provider, secure `__Host-` cookie, exact single HTTPS application origin, exact single-origin CORS, bounded session/nonce settings, server-only Steam publisher/encryption keys, an explicit `STEAM_LOGIN_ALLOWLIST` when `AUTH_PROVIDER=steam`, disabled V1 scheduler, disabled Demo source/experimental CDN, bounded download policy, and non-default render-worker credential are valid. Steam realm and callback are derived from `BACKEND_PUBLIC_URL`; complete OIDC configuration is required only when `AUTH_PROVIDER=oidc`. Demo source/download configuration is API-only. The parser/render queue worker uses a narrower validation path and does not receive browser Steam/OIDC secrets, match-history credentials, provider configuration, or provider secrets. Verified identities resolve through `accounts` and `external_identities` to a stable opaque `owner_v1_...`; browser sessions remain random opaque Redis entries. Unsafe browser mutations require the exact `FRONTEND_PUBLIC_URL` origin. No SteamID64, provider token, Game Authentication Code, Match Sharing Code, encryption metadata, raw identity claim, auth secret, provider source URL, or owner ID belongs in a frontend-visible payload.

Every browser-private auth, demo, upload, replay, coaching, diagnostics, render-job, and media response—including `4xx` failures—sets `Cache-Control: private, no-store` and merges `Cookie, Origin` into `Vary`. Render failures persist and expose only the stable `RENDER_FAILED` or `RENDER_WORKER_UNAVAILABLE` code and safe message; callback-provided error text and background exception strings do not enter the database, replay payload, user JSON, or logs.

### Render Clip and Render Worker

| Variable | Default | Used by | Notes |
| --- | --- | --- | --- |
| `MAX_RENDER_CLIP_SECONDS` | `60` | API | Maximum accepted `render_clip` duration. |
| `RENDER_CLIPS_ENABLED` | `0` | API | Production-only opt-in for user-facing `render/clip` creation and render-job retry; while off those routes return `404` and `/auth/me` reports `capabilities.renderClips=false`. Keep `0` unless an external GPU worker (`RENDER_WORKER_MODE=external`) is deployed. Development/test always allow clips. |
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
| `SAMPLE_DEMO_PATH` | unset | `scripts/cloud_preview_smoke.py` | Absolute path to a local `.dem` for fresh upload/parser smoke. When unset, sample upload is skipped unless required; a production API (`/auth/me` reports `capabilities.devTools=false`) always requires it. |
| `AUTH_SESSION_COOKIE` | unset | `scripts/cloud_preview_smoke.py` | Value of a signed-in account's `__Host-cs2_session` cookie, needed to smoke a production API. Never commit it or paste it into evidence. |
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

When PostgreSQL, Redis, or required worker configuration is unavailable, the endpoint answers HTTP `503` with `{"status":"degraded"}`, so Compose health checks, the reverse proxy, uptime probes, and the smoke scripts can fail on the status code alone. The response does not reveal dependency names, internal URLs, credentials, queue names, or storage paths. It is not a monitoring system.

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
  backend.tests.test_steam_demo_import_config \
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

Component and page tests mount the real dashboard and demo-detail pages against a mocked API client (Vitest + Testing Library on jsdom) and cover the state machines the helper tests cannot see: library loading and empty/error states, status polling, the replay-loading window of a completed demo, failure verdicts and parse retry. Run them for any change to interactive components or page state:

```bash
cd frontend
npm test
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

The current script smoke is a development/test harness. Without `SAMPLE_DEMO_PATH`, it runs health, frontend, mock upload, replay/coaching, render job, private-media routing checks, and a compact development diagnostics summary, then exits successfully with a sample-skip message. It does not replace the production provider/account/owner matrix. Against a production API it needs `AUTH_SESSION_COOKIE` and `SAMPLE_DEMO_PATH`, and it skips the mock upload and `render_clip` steps that `/auth/me` capabilities report as off (see `docs/cloud_preview_deploy_v1.md`). With a configured sample, it uploads through a chunked upload session (the browser's path: create, one token-authorized `PUT` per part, `complete`), waits for parse completion, and prints map, round, coaching, and map calibration/fallback status:

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
- The default Compose frontend is already a production Next.js build (`frontend/Dockerfile.preview`, served by `next start`) and bakes in `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`, because browser requests originate from the host browser, not from the container network. `docker-compose.dev.yml` (used by `scripts/dev.sh`) swaps in the `next dev` image (`frontend/Dockerfile`) for live editing.
- `docker-compose.preview.yml` rebuilds that frontend image with the public API origin and production auth provider.
- Rebuild the frontend image whenever `NEXT_PUBLIC_API_BASE_URL` or `NEXT_PUBLIC_AUTH_PROVIDER` changes because both are bundled at build time.
- Private media always uses the API's owner-scoped `/demos/{demo_id}/media/video` route. Production frontend pages and API/auth/media paths must share one exact HTTPS origin so `__Host-` cookies and CSRF checks protect both API and native video requests.
- The local artifact adapter writes private logical-reference objects under `/data` by default and remains development/test only. Production startup requires the private S3-compatible adapter and never exposes a bucket URL.

### Windows local dev: Docker Desktop crashes when started from a packaged desktop app

On this Windows host Docker Desktop sometimes failed to start: its backend (`com.docker.backend.exe`) crashed before the engine became reachable, and `%LOCALAPPDATA%\Docker\backend.error.json` records a stale-socket rename failing with Win32 error 1920:

```
listening on unix://C:/Users/<user>/AppData/Local/Docker/run/sailor-ingest.sock:
rename ...sailor-ingest.sock ...sailor-ingest.sock.stale: The file cannot be accessed by the system.
```

- **Cause (verified 2026-09-26).** Docker Desktop had been launched from inside a packaged (MSIX) desktop app — the Claude or Codex desktop app running `scripts/start-local.ps1`. A process started as a child of such an app inherits the app's file-system virtualization, and that layer cannot open files carrying the AF_UNIX socket reparse tag. Docker's startup rename of a leftover socket therefore fails with 1920 and the backend crashes; every later start from the same context fails the same way. The same stuck sockets were readable from WSL (`/mnt/host/c/...`), and Docker Desktop started through Explorer came up in seconds — renaming the stale sockets itself — while Riot Vanguard (`vgk`) was running, so the anti-cheat drivers are not the cause. An earlier write-up (2026-09-18) blamed a kernel anti-cheat minifilter; its tests had all run from inside the packaged context and are superseded.
- **Fix.** `scripts/start-local.ps1` now starts Docker Desktop through `explorer.exe`, so it always runs in the normal user session whoever invokes the launcher (double-click, a terminal, or an AI coding app). If Docker Desktop is already running but its engine stays down for 120 s, the launcher ends Docker Desktop, its backend and helper processes, stops the `docker-desktop` WSL distro, waits for them to exit, and starts it once more through Explorer. It does not use `docker desktop stop`, which can hang on a broken backend and later quit the fresh instance; volumes, images and settings are untouched. No stale-socket cleanup is needed: outside the packaged context Docker clears them on start. The old `evict-stale-docker-sockets.ps1` preflight was removed.
- **Rule for automation.** Never start `Docker Desktop.exe` (or `docker desktop start`) as a direct child of a packaged desktop app; use the launcher or `explorer.exe "C:\Program Files\Docker\Docker\Docker Desktop.exe"`. Win32 errors on files under `%LOCALAPPDATA%\Docker\run` seen from such an app are an artifact of its virtualized view, not evidence of damage. Never use "Reset to factory defaults" or delete Docker data for this failure.

## Deployable Boundaries

Ready at the Stage 3 application boundary:

- Steam OpenID 2.0 direct verification, formal account/external-identity mapping, Redis opaque browser sessions, logout revocation, frontend session-expiry handling, and an explicit OIDC compatibility provider.
- AES-GCM protected Steam match-history authorization, owner-scoped manual sharing-code cursor sync, persisted repair/backoff state, Redis owner/global invocation limits plus a Valve-429 publisher breaker, and compact Recent Steam Matches discovery UI.
- Owner-scoped, idempotent Steam match import boundary with truthful lifecycle states and reuse of the existing quarantine/accepted/parser pipeline; the shipped source provider is deliberately disabled.
- Owner-scoped private video GET/HEAD/Range delivery without a public static media mount.
- Demo Library upload, search, status/map filtering, sorting, rename, soft archive, and confirmed permanent delete flows.
- Owner-scoped permanent match deletion (`DELETE /demos/{demo_id}`) and production-only account deletion (`DELETE /auth/account`), both deleting rows in one short transaction. Storage is purged after commit through a durable outbox that the worker drains. Account deletion revokes every session of the owner, and creator commits are fenced against a deleted account. The public `/privacy` page states what is stored and what survives deletion. The protocol is in `docs/data_deletion_v1.md`.
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
- Valve match-history discovery returns only sharing codes. No public supported Demo URL or licensed source is configured; automatic download therefore remains unavailable, and manual `.dem` upload is the reliable fallback. Map/duration/side wins/player metadata must remain absent until parser success.
- `render-worker` fake and manual adapters are not real GPU rendering.
- Object bucket/IAM/resource provisioning remains an external deployment decision; Stage 3 creates no cloud resources or real secrets.
- Stage 3 byte-level intake cannot prove semantic `.dem` validity. The parser now runs in a child process under a wall-clock ceiling and an `RLIMIT_DATA` cap, so a native crash or a runaway allocation costs that demo rather than the worker; CPU, disk, and network confinement remain Stage 5.
- Redis dispatch is now durable in delivery: `BRPOPLPUSH` into a per-consumer processing list plus a TTL lease means a killed worker's in-flight message is recovered by the next worker, and database reconciliation independently rescues rows stuck in `processing` or stranded in `queued` after Redis loss, terminating at `PARSE_MAX_ATTEMPTS` with `PARSE_ABANDONED` so the demo becomes retryable. Steam imports keep their bounded manual requeue after a queued job's 30-second dispatch marker becomes stale, and the worker's queued-to-processing database CAS still makes duplicate Redis delivery harmless. Recovery is bounded by the lease TTL rather than immediate, and parsing is still one worker at a time.
- A hard API process loss in the narrow interval after accepted artifact promotion but before the Demo binding transaction can leave an unbound accepted object. In-process failures are cleaned, but accepted-object orphan enumeration/GC remains storage lifecycle work; quarantine TTL alone does not cover this interval. Deletion sweeps list only the deleted demo's or account's own prefixes, and the worker now also removes quarantine objects older than one hour at most hourly.
- Deleted data is not purged from backups or logs. It remains up to 30 days in remote database dumps and `artifacts-replaced/`, in the newest 14 local dumps, and in size-rotated container logs. Restoring an older dump brings back deletions made after it. The operator must repeat those deletions and remove the pre-restore dump and database within 30 days (`docs/vps_deploy_v1.md` step 7). There is no operator command to delete on a user's behalf.
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
4. Development/test only: create a mock upload and wait for it to complete. Production returns `404` for `POST /uploads/mock`; use the real `.dem` from step 5.
5. If a sample is available, set `SAMPLE_DEMO_PATH` and upload a real `.dem`.
6. Open a demo detail page.
7. Use round review quick jumps and confirm first-person shell/private video, tactical map, timeline, parser markers, and coaching cards stay synchronized.
8. Confirm Replay Contract diagnostics show counts and no unexpected degraded fields for a healthy mock demo.
9. For a failed parse fixture or seeded row, confirm the Dashboard and detail summary show compact failure metadata such as `INVALID_DEMO` or `UNSUPPORTED_PARSER_FORMAT`, and retry availability only when a source artifact exists.
10. Development/test only, or production with `RENDER_CLIPS_ENABLED=1`: click `Generate Clip` on a coaching event.
11. After step 10, confirm render job status appears in the UI and `/demos/{demo_id}/render/jobs`.
12. For render-worker callback validation, run either the fake adapter with `DEV_FAKE_VIDEO_PATH` or the manual adapter flow documented in `render-worker/README.md`.
13. Validate owner A, owner B, anonymous, expired, and revoked sessions across every surface in `docs/production_auth_owner_private_media_v1.md`, including private video GET/HEAD/Range and copied/guessed URL denial.
14. Confirm production `/diagnostics` is `404`, public `/health` is coarse, and `/media/videos/...` is not mounted.
15. For development-mode hosted preview validation, run `python3 scripts/cloud_preview_smoke.py` with `API_BASE_URL` and `FRONTEND_URL` set to the public origins. Add `SAMPLE_DEMO_PATH` and `--require-sample` for strict parser-ingestion validation.
16. Open `/privacy` signed out: it renders without a session and shows the configured region/contact (or the fallback text). The footer with the `隐私说明` link and the not-affiliated-with-Valve line appears on the dashboard, Demo Detail, `/account`, and the sign-in wall. A production edge also passes `scripts/deploy/prod_smoke.sh`: `GET /privacy` returns HTML, and an anonymous `DELETE /auth/account` returns `401` JSON from the API.
17. Permanent match delete. Never do this against real data: the local stack keeps real demos under the default dev owner.
    - Development: create a mock demo under a throwaway owner (`X-Dev-User-Id: del-test-<n>`). Delete it and expect `204`; delete it again and expect `404`. Another owner's id returns `404` and changes nothing.
    - In the browser, delete only a mock demo you just created, from the row menu `删除比赛…` or Demo Detail `删除这场比赛`. Confirm that the row disappears and does not come back on the next poll.
    - Production: delete your own smoke upload. The daily upload count must not go down.
18. Account deletion. Development/test: `DELETE /auth/account` returns `409` `account_deletion_unavailable`, and `/account` shows no delete button. Production, with a dedicated invited test account only: `/account` → `删除账户…` → type `删除账户` → confirm. The done panel appears, the same account's session in a second browser gets `401`, and signing in again yields an empty library.
