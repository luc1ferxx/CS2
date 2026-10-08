# Cloud Preview Deploy V1

This preview path uses Docker Compose with the FastAPI API, Redis worker, PostgreSQL, Redis, and a built Next.js frontend. The default checked-in shape is an explicit development-mode preview. A production-auth candidate requires the selected provider's complete HTTPS/cookie/frontend/CORS configuration and the Steam/account plus owner/private-media acceptance matrices; the script smoke alone is not production sign-off.

For the complete internal reviewer package, including local RC commands, strict sample validation, browser smoke, known limitations, and handoff evidence, use `docs/internal_preview_packaging_v1.md`.

For the production launch on one VPS (Caddy edge, `docker-compose.prod.yml`, bootstrap/deploy/rollback, production smoke, backups and restore drills), use [vps_deploy_v1](vps_deploy_v1.md).

## Selected Preview Shape

Use the default `docker-compose.yml` for local development. Use `docker-compose.preview.yml` as an override when the frontend should run from a production Next.js build:

```bash
docker compose -f docker-compose.yml -f docker-compose.preview.yml up --build
```

Preview services:

| Service | Purpose | Public exposure |
| --- | --- | --- |
| `frontend` | Next.js review UI, built with `frontend/Dockerfile.preview` | Public preview URL |
| `api` | FastAPI API, auth, health, uploads, replay/coaching/render endpoints, owner-scoped private media | Public API URL |
| `worker` | Redis-backed parse/mock-render/render-clip worker | Private service |
| `postgres` | Metadata and job database | Private service |
| `redis` | Parse/render queue | Private service |

The app still stores uploads, replay blobs, summaries, and videos on mounted local volumes. Do not treat this preview as durable object storage. Parts of unfinished chunked uploads always live on the API host's own disk, in every shape including production: the named volume `upload-staging` is mounted at `/data/upload-staging` in both `api` and `worker` (the worker's hourly maintenance sweeps it). A session that completes moves its bytes through the normal intake into artifact storage; `docker compose down -v` drops unfinished uploads along with the other volumes.

## Public URL Contract

Set these before building a hosted preview:

```bash
export FRONTEND_URL=https://cs2-preview.example.com
export NEXT_PUBLIC_API_BASE_URL=https://cs2-preview.example.com
export NEXT_PUBLIC_AUTH_PROVIDER=steam
export FRONTEND_PUBLIC_URL=https://cs2-preview.example.com
export BACKEND_PUBLIC_URL=https://cs2-preview.example.com
export CORS_ORIGINS=https://cs2-preview.example.com
```

For a production-auth candidate, also configure server-side values through the deployment secret/config system (placeholder origins shown; do not commit real values):

```bash
export AUTH_MODE=production
export AUTH_PROVIDER=steam
export AUTH_COOKIE_SECURE=1
export STEAM_DEMO_PROVIDER=disabled
export STEAM_DEMO_EXPERIMENTAL_REPLAY_CDN_ENABLED=0
```

Set required `STEAM_WEB_API_KEY`, a random `STEAM_CREDENTIAL_ENCRYPTION_KEY`, and the non-default `RENDER_WORKER_TOKEN` only through server-side secret injection. `STEAM_WEB_API_KEY` enriches display metadata and authorizes the official match-history publisher call; it is still not part of Steam OpenID verification. Set `STEAM_LOGIN_ALLOWLIST` to the invited Steam ID64s, comma separated, or to `*` to deliberately open sign-in to every Steam account. If the legacy-compatible OIDC provider is selected, also provide the complete `OIDC_*` configuration documented in `docs/production_auth_owner_private_media_v1.md`. Production API startup rejects malformed/missing Steam keys, a missing or malformed Steam login allowlist, the checked-in development encryption key, an enabled V1 scheduler, any Demo provider other than `disabled`, an enabled experimental replay-CDN switch, malformed bounded download configuration, malformed selected identity-provider configuration, and the development worker token. The parser/render queue worker uses its separate narrow validation path and does not receive browser Steam/OIDC, match-history, or Demo-source secrets/configuration.

`NEXT_PUBLIC_API_BASE_URL` and `NEXT_PUBLIC_AUTH_PROVIDER` are baked into the frontend browser bundle during `next build`, so rebuild the frontend image whenever either changes. The public provider selector must match server `AUTH_PROVIDER`. Steam publisher/encryption keys have no `NEXT_PUBLIC_*` form.

The preview override does not provision an edge proxy or cloud resource; for a single VPS, `deploy/Caddyfile` in `docker-compose.prod.yml` implements this routing (see [vps_deploy_v1](vps_deploy_v1.md)). A production candidate must supply same-origin routing: frontend pages and static assets, including frontend `/auth/callback`, go to Next.js; `/auth/steam/login`, `/auth/steam/callback`, `/auth/me`, `/auth/logout`, compatibility auth routes, `/steam/*`, `/demos`, every path below `/demos/{id}/`, `PATCH /demos/{id}`, `/uploads/*`, `/coaching/*`, `/health`, and render-worker API paths go to FastAPI on the same public origin, while `GET /demos/{id}` is the Next.js review page. The backend Steam callback and frontend completion page must remain distinct. The checked-in two-port localhost layout remains development mode.

User-facing video metadata contains only `/demos/{demo_id}/media/video`, which the frontend resolves against `NEXT_PUBLIC_API_BASE_URL`. The API checks the opaque session and owner again for every GET/HEAD/Range request. There is no public `/media/videos` mount. Production must route frontend pages plus API/auth/media paths through the same exact HTTPS origin; split subdomains fail runtime validation.

`GET /health` returns only HTTP `200` `{"status":"ok"}` or, when any dependency check fails, HTTP `503` `{"status":"degraded"}`, and does not echo origins, dependencies, or storage paths.

`GET /diagnostics` is available only in development/test previews. Production returns `404`; use the authenticated owner-scoped `/demos/{demo_id}/diagnostics` for user-visible demo diagnosis and defer protected operational diagnostics to the observability stage.

## Environment Files

Examples are checked in for each runtime:

- `.env.example`: root Compose preview/local defaults
- `backend/.env.example`: API and worker settings for running outside Compose
- `frontend/.env.example`: browser API origin
- `render-worker/.env.example`: external fake/manual worker runner settings

Required preview values:

| Variable | Used by | Notes |
| --- | --- | --- |
| `DATABASE_URL` | API, worker | Use the Compose `postgres` hostname in containers. |
| `REDIS_URL` | API, worker | Use the Compose `redis` hostname in containers. |
| `AUTH_MODE` | API | Explicit `development`, `test`, or `production`. Production never falls back to a dev owner. |
| `AUTH_PROVIDER` | API | Required explicitly in production: `steam`, or `oidc` for the compatibility provider. |
| `FRONTEND_PUBLIC_URL` | API | Trusted frontend callback origin. Production requires it to equal `BACKEND_PUBLIC_URL` exactly and be the sole value in `CORS_ORIGINS`. |
| `BACKEND_PUBLIC_URL` | API, worker | Public API origin; exact HTTPS application origin in production and not echoed by `/health`. |
| `NEXT_PUBLIC_API_BASE_URL` | Frontend | Public API origin for credentialed fetches and private media resolution. This is public configuration, not a secret. |
| `NEXT_PUBLIC_AUTH_PROVIDER` | Frontend | Public login selector; must equal backend `AUTH_PROVIDER`. |
| `CORS_ORIGINS` | API | Credentialed frontend origin. Production requires exactly `FRONTEND_PUBLIC_URL` and rejects wildcard, stale, or sibling origins. |
| `ARTIFACT_STORAGE_ROOT` and category dirs | API, worker | Local volume roots for uploads, replay blobs, summaries, and videos. |
| `STEAM_AUTH_STATE_COOKIE_NAME`, `STEAM_OPENID_NONCE_TTL_SECONDS` | API, Redis | Steam state cookie and assertion freshness/replay window. |
| `STEAM_WEB_API_KEY` | API | Required production server-only GetPlayerSummaries/match-history publisher key. Never prefix with `NEXT_PUBLIC_` or send to workers. |
| `STEAM_LOGIN_ALLOWLIST` | API | Required by the preview Compose shape: comma-separated invited Steam ID64s, or `*` to allow every Steam account (with `AUTH_PROVIDER=oidc`, set `*`). Uninvited Steam sign-ins land on `/auth/callback?error=not_invited` without creating an account; removing an ID ends that user's live sessions. After editing it, redeploy the API container so it re-reads the environment (`docker compose -f docker-compose.yml -f docker-compose.preview.yml up -d`); a plain `docker compose restart` keeps the old value. |
| `STEAM_CREDENTIAL_ENCRYPTION_KEY`, `STEAM_CREDENTIAL_ENCRYPTION_KEY_VERSION` | API | AES-256-GCM key material/version for authorization, cursor, and discovered sharing codes. |
| `STEAM_SYNC_MAX_MATCHES`, `STEAM_SYNC_TIMEOUT_SECONDS`, `STEAM_SYNC_RETRY_BASE_SECONDS`, `STEAM_SYNC_RETRY_MAX_SECONDS` | API | Bounded manual-sync and persisted-backoff policy. |
| `STEAM_SCHEDULED_SYNC_ENABLED` | API | Must remain false in V1; no scheduler is registered. |
| `STEAM_DEMO_PROVIDER`, `STEAM_DEMO_EXPERIMENTAL_REPLAY_CDN_ENABLED` | API | Must remain `disabled` / false; this build has no licensed automatic Demo source. |
| `STEAM_DEMO_DOWNLOAD_ALLOWED_HOSTS` | API | Leave empty for the disabled provider; exact hosts require a separately reviewed licensed adapter. |
| `STEAM_DEMO_DOWNLOAD_*` limits | API | Bounded size, redirects, deadlines, owner/global concurrency, and expiring lease policy; Redis stores only the resulting lease state, and these values never enter frontend/worker configuration. |
| `OIDC_*` | API | Required only when `AUTH_PROVIDER=oidc`; see the compatibility contract. |
| `AUTH_COOKIE_SECURE`, `AUTH_SESSION_COOKIE_NAME` | API | Secure opaque session cookie controls. Production requires secure cookies. |
| `AUTH_SESSION_TTL_SECONDS`, `AUTH_SESSION_MAX_AGE_SECONDS`, `AUTH_LOGIN_TTL_SECONDS` | API, Redis | Session idle window (renewed while in use), its absolute cap from sign-in, and the one-time login attempt lifetime. |
| `DEV_USER_ID` | API | Development/test owner harness only; `X-Dev-User-Id` is not a production identity source. |
| `DEMO_UPLOAD_DAILY_LIMIT`, `DEMO_ACTIVE_PARSE_LIMIT`, `PARSE_QUEUE_GLOBAL_LIMIT` | API | Production-only beta quotas (defaults `10`, `2`, `50`; `0` disables one): new demos per owner in a rolling 24h window, demos per owner still queued/parsing/analyzing, and in-flight demos across all owners. Uploads over a limit are refused before the body is read and again in the route (a chunked upload session: advisory at creation, authoritative at `complete`); parse retries honour the two in-flight caps. Rejections are `429`/`503` with `Retry-After`. |
| `UPLOAD_STAGING_ROOT`, `UPLOAD_PART_BYTES`, `UPLOAD_MAX_PARALLEL_PARTS`, `UPLOAD_PART_POOL`, `UPLOAD_SESSION_TTL_SECONDS`, `UPLOAD_SESSION_GLOBAL_LIMIT`, `UPLOAD_STAGING_MIN_FREE_BYTES` | API (`UPLOAD_STAGING_ROOT` also worker) | Chunked upload sessions (all modes): staging directory, part size (default 8 MiB), parallel parts per session (4), process-wide part pool (8), hard session TTL from creation (24 h), open sessions across all owners (6), and the free-space floor a new session needs on the staging disk (5 GiB; a small Docker Desktop disk can trip `503 upload_storage_full` locally). See `configuration_reference_v1.md`. |
| `MAX_RENDER_CLIP_SECONDS` | API, worker | Render clip duration guard. |
| `RENDER_WORKER_TOKEN` | API, render-worker | Separate worker service credential. Production rejects the development default. |
| `RENDER_CLIPS_ENABLED` | API | Production-only opt-in (default `0`) for user-facing `render/clip` creation and retry; while off those routes return `404` and `/auth/me` reports `capabilities.renderClips=false`. Keep `0` unless an external GPU worker (`RENDER_WORKER_MODE=external`) is deployed. Development/test always allow clips. |
| `API_BASE_URL` | render-worker | Public API origin used by `render-worker/runner.py`. |
| `DEV_FAKE_VIDEO_PATH` | render-worker | Optional MP4 for fake adapter callback validation. |
| `CS2_INSTALL_DIR`, `STEAM_USER_DATA_DIR`, `CS2_MANUAL_OUTPUT_FILENAME` | render-worker | Manual adapter instruction metadata only. |
| `AUTH_SESSION_COOKIE` | smoke script | Value of a signed-in account's `__Host-cs2_session` cookie; required to smoke a production preview, where `X-Dev-User-Id` selects no owner. Never commit it or paste it into evidence. |
| `SAMPLE_DEMO_PATH` | smoke script | Optional absolute path to a local sample `.dem` for fresh upload/parser validation; required when `/auth/me` reports `capabilities.devTools=false` (production). Archive samples are development compatibility only. |
| `SAMPLE_DEMO_NAME` | smoke script | Optional display name applied after sample upload through `PATCH /demos/{demo_id}`. |
| `REQUIRE_SAMPLE_DEMO` | smoke script | Set to `1` when smoke should fail if no sample is configured. Equivalent CLI flag: `--require-sample`. |

## Smoke Checklist

For release-candidate sign-off, use the full checklist in `docs/release_candidate_qa_v1.md`. For reviewer handoff evidence, use `docs/internal_preview_packaging_v1.md`. The script smoke below covers the explicit development-mode API/frontend/mock/render/private-media projection and optional sample upload; it does not acquire a production Steam/OIDC session or replace the account/owner matrices. Against the `AUTH_MODE=production` preview above, sign in with an invited account first and export `AUTH_SESSION_COOKIE` (that session's `__Host-cs2_session` value) plus `SAMPLE_DEMO_PATH`: the script reads `/auth/me` capabilities, skips the mock upload when `devTools=false` (the sample becomes the replay/coaching/media demo, so it fails without `SAMPLE_DEMO_PATH`) and skips `render_clip` when `renderClips=false`, printing each skip. With `renderClips=true` the clip names a POV player whose id is a 17-digit SteamID64 (the first coaching event's player or evidence player first), because the external renderer refuses clips without one; a production sample without such a player fails the smoke. Every run uploads the sample through a chunked upload session, which counts toward that account's `DEMO_UPLOAD_DAILY_LIMIT`.

API/script smoke:

```bash
API_BASE_URL="$NEXT_PUBLIC_API_BASE_URL" \
FRONTEND_URL="$FRONTEND_URL" \
python3 scripts/cloud_preview_smoke.py
```

When no sample is configured against a development/test API, the script prints a skip message and still exits successfully after the mandatory mock upload, replay/coaching, render job, private-media route checks, and compact development diagnostics summary. If diagnostics is unavailable, the script keeps the original failure visible. Add a real demo parse check when a sample is available:

```bash
SAMPLE_DEMO_PATH=/absolute/path/to/sample.dem \
API_BASE_URL="$NEXT_PUBLIC_API_BASE_URL" \
FRONTEND_URL="$FRONTEND_URL" \
python3 scripts/cloud_preview_smoke.py
```

Require the sample for stricter preview validation:

```bash
SAMPLE_DEMO_PATH=/absolute/path/to/sample.dem \
API_BASE_URL="$NEXT_PUBLIC_API_BASE_URL" \
FRONTEND_URL="$FRONTEND_URL" \
python3 scripts/cloud_preview_smoke.py --require-sample
```

Place local samples under ignored directories such as `sample-demos/`, `samples/`, or `.local/samples/`, then set `SAMPLE_DEMO_PATH` to the absolute path. The sample upload uses the browser's chunked upload session path: `POST /uploads/sessions` (replacing an unfinished session left by an earlier smoke run), one `PUT /uploads/sessions/{id}/parts/{i}` per part with the exact `Content-Length`, the session's `X-Upload-Token`, `X-Part-SHA256` and the frontend `Origin` (no cookie on parts), then `POST .../complete`, sent again every few seconds while it answers `202` (a repeated `complete` is what takes over a lease an API restart ended) and re-sending any part reported missing. It prints one line with the part count, bytes and seconds, then waits for parser completion and prints map, round, coaching, and map calibration/fallback status. The legacy single-request `POST /uploads/demo` stays available for ad hoc `curl -F` uploads. Existing parsed rows can prove detail-page compatibility, but they do not prove fresh upload/parser ingestion.

Manual browser smoke:

1. Open the public dashboard URL. A production candidate must show the shared auth boundary, complete Steam OpenID sign-in, return through the frontend callback, show only compact account metadata, and leave no assertion/session token in the URL or browser storage.
2. On a clean or filtered library, confirm empty/loading/no-result states show direct actions for create mock, upload `.dem`, refresh, clear filters, or show archived.
3. Create a mock upload and wait until it completes. Production has no mock path (`POST /uploads/mock` returns `404`); upload a real `.dem` instead.
4. Open the demo detail page from the post-create notice or table action.
5. Confirm the compact detail summary shows file, map, calibration/fallback, rounds, coaching count, parser status, media status, and latest render status.
6. Verify play/pause, seek, speed, round selection, coaching card click-to-seek, tactical map sync, parser markers, replay diagnostics, and degraded states.
7. Click `Generate Clip`. Production offers it only with `RENDER_CLIPS_ENABLED=1`.
8. Confirm the render job appears in the UI and `GET /demos/{demo_id}/render/jobs`; without an external GPU worker, the expected failure text is `GPU worker not connected for render_clip`.
9. In development/test, check `GET /diagnostics` for safe worker heartbeat, job counts, and recent failure summaries. In production, confirm that system endpoint returns `404` while demo diagnostics remain authenticated and owner-scoped, and that the dev/QA routes (`/uploads/mock`, manual video upload/calibration, `/render/mock`) and `/docs`, `/redoc`, `/openapi.json` also return `404`.
10. If video exists, check credentialed `/demos/{demo_id}/media/video` with GET, HEAD, and a byte range. A copied URL in owner B or anonymous context must return no bytes; if media is missing, denied, or the session expires, the UI must keep the synced 2D/mock shell usable.
11. Sign out and prove the old session no longer loads library data or media. Run the owner A/B/anonymous/expired/revoked matrix in `docs/production_auth_owner_private_media_v1.md`.
12. If a sample `.dem` exists, upload it or run smoke with `SAMPLE_DEMO_PATH`, then confirm the same detail-page sync behavior and compact parser failure copy if the sample is invalid.

For local preview RC checks, `./scripts/rc_check.sh` runs the non-browser command sequence against `API_BASE_URL` and `FRONTEND_URL`, then prints the manual browser checklist reminder. Set those variables to public preview origins when using it outside localhost against a development-mode API; a production API returns `404` for `/diagnostics`, so run `scripts/cloud_preview_smoke.py` directly as above instead.

## Render Worker Preview

The in-repo worker still marks unprocessed `render_clip` jobs failed with the clear "GPU worker not connected" message. For callback validation, run the external skeleton against the public API:

```bash
API_BASE_URL="$NEXT_PUBLIC_API_BASE_URL" \
RENDER_WORKER_TOKEN="$RENDER_WORKER_TOKEN" \
python3 render-worker/runner.py dry-run {job_id}
```

Use `DEV_FAKE_VIDEO_PATH` for the fake MP4 adapter, or `prepare-job` / `complete-prepared-job` for the manual operator flow. These adapters do not launch Steam, CS2, OBS, ffmpeg, or control a local game client.

## Limitations

- `DEV_USER_ID` and `X-Dev-User-Id` are an explicit development/test harness and do not select an owner in production.
- The checked-in Compose preview defaults are not a registered Steam OpenID production origin or a production public-beta environment. Production requires HTTPS routing, secure secrets, real Steam callback smoke, and the full acceptance matrix.
- Local volumes are not durable object storage; do not store large artifacts in PostgreSQL.
- Uploaded `.dem` files are untrusted input. Archive ingestion, where available, is development compatibility rather than the primary product path. Real match demos can contain player data or licensed match content; only use samples you are allowed to store and upload to the preview.
- Do not commit `.dem`, demo archives, generated replay blobs, or media outputs; keep them in ignored local sample/storage paths.
- Fake/manual render-worker flows prove the callback contract only; real first-person rendering still belongs to a controlled external GPU worker.
- The API and worker containers must not run CS2, Steam, OBS, ffmpeg automation, OpenAI calls, or screen recording.
- Development `/diagnostics` is a compact preview/debugging aid; it is disabled in production and is not observability, alerting, audit logging, or raw parser trace storage.

## Rollback and Redeploy

For a Compose preview, redeploy by rebuilding with the desired env:

```bash
docker compose -f docker-compose.yml -f docker-compose.preview.yml up --build -d
```

Rollback by checking out the previous Git revision and running the same command. If schema/data compatibility is uncertain, keep the named Docker volumes before rollback and test `/health`, the dashboard, replay load, and render job listing against the restored revision.

Stop the preview without deleting data:

```bash
docker compose -f docker-compose.yml -f docker-compose.preview.yml down
```

Remove preview volumes only when you intentionally want a clean environment:

```bash
docker compose -f docker-compose.yml -f docker-compose.preview.yml down -v
```
