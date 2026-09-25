# Release Candidate QA V1

This runbook defines the repeatable release-candidate validation pass for local Docker development, short-lived previews, Steam-first production identity/account/owner authorization, encrypted match-history discovery, and private video access. Passing it does not complete Demo source download, reliable-job, parser-isolation, observability/backup, or beta-corpus stages.

## Scope

RC QA validates that the demo-first review flow still works:

1. Local checks compile, test, lint, typecheck, and build.
2. Docker services build and start.
3. Public API health is coarse; production system diagnostics are disabled; demo diagnostics remain authenticated and owner-scoped.
4. Steam OpenID assertion/discovery/direct verification, account mapping, Redis opaque sessions, logout revocation, encrypted match-history connection/sync, and frontend session-expiry handling pass focused tests and browser smoke; the explicitly selected OIDC compatibility path remains covered.
5. Owner A/B/anonymous/invalid/expired/revoked requests are denied or allowed consistently across every user surface.
6. Private video GET/HEAD/Range rechecks session, owner, and safe artifact binding without a public static-media bypass.
7. Development Cloud Preview smoke creates a mock demo, opens replay/coaching data, creates a `render_clip` job, checks private-media projection, and prints development diagnostics.
8. Optional sample `.dem` smoke proves fresh real-demo upload, Redis parse dispatch, replay storage, rules analysis, and map calibration/fallback metadata.
9. Manual browser smoke verifies the dense Demo Library and Demo Detail review workflows on desktop and mobile.

## Non-Goals

- No OpenAI or LLM coaching.
- No password/account-management system, provider-specific SDK, billing, or team collaboration.
- No real CS2, Steam, OBS, ffmpeg, screen recording, or local game-client control in API/worker containers.
- No object-storage migration or production media durability work.
- No production observability or backup/restore implementation, and no CPU/disk/network sandbox around the parser beyond its child-process isolation, wall-clock ceiling, and memory cap. Automatic durable queue recovery and stale-processing reconciliation are implemented and are bounded by the lease TTL rather than immediate; Steam import still has only its focused manual queued-job requeue and duplicate-execution CAS, and the account schema has only its focused forward-only migration.
- No checked-in `.dem`, video, replay blob, parser dump, or generated media artifacts.

## Required Local RC Checklist

Run from a clean `main` checkout unless you are intentionally validating an uncommitted candidate:

```bash
git status --short
git pull origin main
```

Then run the non-browser gates:

```bash
PYTHONPATH=backend python3 -m unittest \
  backend.tests.test_database_migrations \
  backend.tests.test_steam_auth \
  backend.tests.test_production_auth \
  backend.tests.test_private_media \
  backend.tests.test_auth_owner_boundary \
  backend.tests.test_diagnostics
cd frontend
node lib/auth.test.mjs
node lib/private-media.test.mjs
cd ..
./scripts/verify.sh
docker compose build
docker compose up -d
curl http://localhost:8000/health
# development/test only; production must return 404:
curl http://localhost:8000/diagnostics
API_BASE_URL=http://localhost:8000 FRONTEND_URL=http://localhost:3000 python3 scripts/cloud_preview_smoke.py
```

You can run the same non-browser sequence with:

```bash
./scripts/rc_check.sh
```

`scripts/rc_check.sh` remains a development-mode harness. It does not acquire independent production Steam/OIDC sessions and therefore does not replace the account/owner matrix, private-media denial tests, or manual browser QA.

## Optional Sample `.dem` Validation

Keep real samples in ignored local folders such as `sample-demos/`, `samples/`, or `.local/samples/`.

```bash
export SAMPLE_DEMO_PATH="$PWD/sample-demos/sample.dem"
export SAMPLE_DEMO_NAME="Local Sample Demo"
```

Run optional sample smoke:

```bash
SAMPLE_DEMO_PATH="$SAMPLE_DEMO_PATH" python3 scripts/cloud_preview_smoke.py
```

Run strict sample smoke when RC validation must prove fresh real-demo ingestion:

```bash
SAMPLE_DEMO_PATH="$SAMPLE_DEMO_PATH" python3 scripts/cloud_preview_smoke.py --require-sample
```

Or make `scripts/rc_check.sh` fail when no sample is configured:

```bash
REQUIRE_SAMPLE_DEMO=1 SAMPLE_DEMO_PATH="$SAMPLE_DEMO_PATH" ./scripts/rc_check.sh
```

When `SAMPLE_DEMO_PATH` is absent and strict mode is not requested, sample validation is skipped with a clear message.

## Cloud Preview RC Flow

Build a hosted preview with public origins set before the frontend image is built:

```bash
export FRONTEND_URL=https://cs2-preview.example.com
export NEXT_PUBLIC_API_BASE_URL=https://cs2-preview.example.com
export FRONTEND_PUBLIC_URL=https://cs2-preview.example.com
export BACKEND_PUBLIC_URL=https://cs2-preview.example.com
export CORS_ORIGINS=https://cs2-preview.example.com
export AUTH_PROVIDER=steam
export NEXT_PUBLIC_AUTH_PROVIDER=steam

docker compose -f docker-compose.yml -f docker-compose.preview.yml up --build -d
```

Check the public API and run smoke against the public URLs:

```bash
curl "$NEXT_PUBLIC_API_BASE_URL/health"
# development/test preview only:
curl "$NEXT_PUBLIC_API_BASE_URL/diagnostics"

API_BASE_URL="$NEXT_PUBLIC_API_BASE_URL" \
FRONTEND_URL="$FRONTEND_URL" \
python3 scripts/cloud_preview_smoke.py
```

For a production-auth RC, set `AUTH_MODE=production`, explicitly set `AUTH_PROVIDER=steam` (or compatibility `oidc`) and matching `NEXT_PUBLIC_AUTH_PROVIDER`, secure `__Host-` cookies, one exact HTTPS origin for frontend/API/auth/media routing, a real server-only `STEAM_WEB_API_KEY`, `STEAM_LOGIN_ALLOWLIST` (invited Steam ID64s, or `*` to open Steam sign-in), random `STEAM_CREDENTIAL_ENCRYPTION_KEY`, disabled V1 scheduler, `STEAM_DEMO_PROVIDER=disabled`, `STEAM_DEMO_EXPERIMENTAL_REPLAY_CDN_ENABLED=0`, the selected identity provider's complete configuration, and a non-default render-worker service credential through the deployment secret/config system. Verify that `/diagnostics`, the dev/QA routes (`/uploads/mock`, manual video upload/calibration, `/render/mock`), and `/docs`/`/openapi.json` return `404`, and that `render/clip` returns `404` unless `RENDER_CLIPS_ENABLED=1`; do not run the development script as a substitute for independently authenticated owner A/B browser/API sessions.

If a sample is available for the preview environment:

```bash
SAMPLE_DEMO_PATH=/absolute/path/to/sample.dem \
API_BASE_URL="$NEXT_PUBLIC_API_BASE_URL" \
FRONTEND_URL="$FRONTEND_URL" \
python3 scripts/cloud_preview_smoke.py --require-sample
```

## Production Identity and Owner Matrix

Use two independent Steam identities, A and B (or two identities from the explicitly selected compatibility provider), plus anonymous, invalid, expired, and explicitly logged-out/revoked browser sessions. Do not simulate this production gate with `X-Dev-User-Id`.

For demos owned separately by A and B, exercise all of these surfaces:

- Library list; status; replay; coaching; demo diagnostics.
- Rename; archive; unarchive.
- Mock upload; real `.dem` upload; parser retry.
- Video status; development/QA video upload; calibration.
- Mock render; `render_clip`; render-job list.
- Private video full GET, HEAD, satisfiable Range, and unsatisfiable Range.
- Steam connection read/write/delete, manual sync, retry/repair state, Recent Steam Matches list, and owner-scoped Demo import attempt.

Expected results:

- Each owner sees and mutates only their own rows. Cross-owner resource IDs return the same generic `404` as unknown IDs and cause no row, job, metadata, or artifact mutation.
- Anonymous, invalid, expired, and revoked sessions return `401` before user data or media bytes are returned and cause no mutation.
- Production ignores/rejects `X-Dev-User-Id`; a valid A session plus a B header remains A.
- A copied private-media URL fails for B and anonymous sessions. Legacy `/media/videos/...`, traversal, another demo's storage reference, leaf or parent-directory symlinks, post-validation path replacement, and missing files return no foreign bytes or local/storage-key details.
- Logout deletes the server-side Redis session and clears the cookie. Reusing the old cookie fails.
- Invalid signature or algorithm, issuer, audience, nonce, timestamps, missing required claims, unknown JWKS key, reused/mismatched state, and unsafe or oversized `return_to` inputs are denied or reduced to the safe dashboard target.
- Unsafe cookie-authenticated mutations with a missing or untrusted `Origin` receive `403` and create no row, job, metadata, or artifact change; render-worker service calls remain on their independent credential boundary.
- User-facing demo/replay/video payloads contain no `owner_id`, `storageKey`, unknown internal replay fields, `local://`, absolute local path, issuer, subject, or token; replay/source keys and replay `demoId` remain bound to the requested demo.
- Steam connection/match payloads contain no SteamID64, Game Authentication Code, Match Sharing Code, Web API key, ciphertext, nonce, fingerprint, or fabricated map/score/player fields. Disconnect A deletes only A's connection/discovery data and does not delete A's independently owned parsed demos or B's rows.
- The default disabled Demo provider makes import fail with fixed unavailable/manual-upload guidance before any outbound download; unknown/foreign match ids return generic `404` and cause no decrypt, network, artifact, Demo, or job mutation.
- Production startup rejects more than one CORS origin, and a stale or sibling configured origin cannot read credentialed responses or pass the unsafe-method origin gate.
- Every auth/session and owner-private JSON/media response, including failures, has `Cache-Control: private, no-store` and `Vary` containing `Cookie` and `Origin`; a shared-cache harness never replays owner A content to owner B.
- Render callback and worker exception probes containing fake local paths, credentials, and traceback markers persist and return only `RENDER_FAILED`/`RENDER_WORKER_UNAVAILABLE` with safe copy; the injected text is absent from DB, replay/video JSON, render-job JSON, and logs.

Record the status/body summary and mutation check for each cell. The concise reference matrix is in `docs/production_auth_owner_private_media_v1.md`.

## Manual Browser Smoke

Open `/dashboard` in the target frontend and verify:

- Anonymous production load shows the selected-provider login boundary; Steam sign-in completes through OpenID and the frontend callback without exposing assertion/state/session/SteamID64 in the final URL or browser storage. Callback query strings are redacted in Uvicorn and ingress logs.
- Dashboard and Demo Detail use the same session boundary; expiry shows a clear `Session expired` recovery action, and sign-out returns to the anonymous boundary.
- Recent Steam Matches accepts exactly the two user authorization codes, clears them after submission, supports Sync now/refresh/disconnect, and shows only real source/discovered/status data. Exercise caught-up, repair, retry, empty, disconnected, and disabled-import states where possible; manual `.dem` upload remains visible and usable when no Demo source exists.
- Do not supply a Steam password, Steam Guard code, browser cookie, private-page session, or personal account secret for automatic-Demo QA. A live automatic source test is out of scope until a licensed Provider contract is approved.
- Keep live credentials out of evidence; rely on the automated Stage 2 regressions for the 3/owner/minute and 30/global/minute Redis limits, shared 429 breaker, concurrent lease claim, and oversized response/body rejection.
- Dashboard loads with no current console errors.
- Empty, loading, fetch-failed, archived-only, and search/filter no-result states are clear when practical to exercise.
- Mock upload creates a demo and the post-create notice/table action opens it. Production hides both `示例比赛` buttons and the Demo Detail mock-render/manual video tools because `/auth/me` reports `capabilities.devTools=false`.
- Real/sample `.dem` upload parses and opens when `SAMPLE_DEMO_PATH` or another approved local sample is available.
- Corrupt `.dem` failure shows compact parser metadata such as `INVALID_DEMO` without stack traces or local paths.
- Demo Library search, status/map filters, sort order, rename, archive, and show archived work.
- Demo Detail summary is readable: file, map, calibration/fallback, rounds, coaching count, parser status/failure, media status, and render status.
- Replay play/pause, seek, and speed controls work.
- Round review quick jumps update the shared tick/round state.
- Tactical map syncs with timeline/replay, and map calibration/fallback is visible.
- Timeline parser and coaching markers click-to-seek.
- Coaching severity/rule/search filters and event cards work.
- The replay's `生成这一刻的视频` button (and a coaching card's `生成视频`) creates a `render_clip` job. In `fallback` render-worker mode a local no-GPU run appears as `GPU worker not connected for render_clip`; in `external` mode an offline renderer instead shows as `render_worker.connected=false`, with the job left `queued` until `RENDER_CLIP_QUEUE_TIMEOUT_SECONDS` (default 30 min) elapses, after which the worker's queue sweep fails it as `RENDER_QUEUE_TIMED_OUT` so the retry button becomes usable.
- `RenderOperatorPanel` shows latest job status, tick range, output, and compact errors. With `capabilities.renderClips=false` (production without `RENDER_CLIPS_ENABLED=1`) the clip buttons and this panel are hidden, and saved clips still play from `已保存的视频`.
- Production beta: an uninvited Steam account lands on `/auth/callback?error=not_invited` showing `暂未开放` with a link back to `/` and no sign-in retry; an upload refused by a quota shows the Chinese limit copy (the daily limit includes the wait), which stays visible while the library polls.
- The private `/demos/{demo_id}/media/video` source uses the session cookie, supports seek via `206`, and never falls back to `/media/videos/...`; missing/denied media preserves the synchronized 2D/mock shell.
- Desktop and mobile widths do not show incoherent horizontal overflow, clipped controls, or current console errors.

## Safe Preview Boundaries

RC sign-off must preserve these boundaries:

- Production identity is explicitly selected Steam OpenID or compatibility OIDC with account/external-identity mapping and one opaque Redis session; `DEV_USER_ID` and `X-Dev-User-Id` remain development/test-only.
- User media is private and demo-scoped. No public static `/media/videos` mount, tokenized query string, or user-facing storage key is allowed.
- Render-worker service credentials remain separate from browser identity.
- Fake/manual render-worker flows are not real GPU rendering.
- Local filesystem storage and Docker volumes are preview/MVP storage, not durable production object storage.
- Uploaded demos are untrusted input.
- Coaching is deterministic rules output; no OpenAI/LLM workflow is in scope.
- Do not commit `.dem`, video, replay, parser dump, local storage, or generated media artifacts.

## Completion Evidence

For a release-candidate handoff, record:

- Git commit SHA and `git status --short` output.
- Non-browser command output, or `./scripts/rc_check.sh` output.
- Whether optional sample smoke was skipped, optional, or strict.
- Production Steam assertion/state/discovery/replay negative-case results, compatibility OIDC results when selected, and the complete owner A/B/anonymous/expired/revoked matrix, including no-mutation evidence.
- Private media GET/HEAD/Range, copied/guessed URL, legacy static path, traversal, symlink, logout, and expiry results.
- Manual browser smoke result, including callback/session-expired/sign-out behavior, desktop/mobile viewport coverage, screenshots, and known limitations.
- Coarse `/health` status and proof that system `/diagnostics` is `404` in production while demo diagnostics remain owner-scoped.
