# Production Auth, Owner Authorization, and Private Media V1

This document freezes the Stage 2 security contract for the rules-based 2D public beta. It is provider-neutral: the application uses OpenID Connect (OIDC) standards and does not depend on a vendor SDK. Stage 2 does not add object storage, queue recovery, parser sandboxing, database migrations, production observability, backup automation, LLM coaching, or GPU rendering.

## Decision

Production browser identity uses OIDC Authorization Code flow with PKCE. The backend validates the returned identity token against the configured issuer, audience, nonce, timestamps, signature, algorithm, and JWKS before it creates an opaque Redis-backed session. Browser requests carry only an `HttpOnly` session cookie; provider tokens and raw identity claims are not exposed to frontend JavaScript, URLs, API responses, PostgreSQL, or local storage.

Every user API derives `owner_id` from the trusted session. Existing owner-scoped database queries remain the authorization boundary. Demo metadata, replay, coaching, demo diagnostics, upload/retry actions, render actions, and video bytes are private by default.

## Runtime Modes and Fail-Closed Rules

`AUTH_MODE` must be set explicitly to `development`, `test`, or `production`. An empty or unsupported value stops startup validation.

- The production API requires complete, well-formed HTTPS OIDC configuration, secure `__Host-` cookies, one exact HTTPS application origin shared by frontend and API, exactly that single CORS origin, Redis, and a non-default render-worker credential. Missing or insecure configuration fails startup; production never falls back to `DEV_USER_ID`.
- The queue worker validates its own narrower runtime: explicit `AUTH_MODE`, plus a non-default `RENDER_WORKER_TOKEN` in production. It does not need browser OIDC endpoints, client credentials, or cookie configuration.
- `development` and `test` retain `DEV_USER_ID` and `X-Dev-User-Id` only as an explicit local/test harness. They are not accepted as proof of identity in production.
- An absent, unknown, expired, or revoked session returns `401 Authentication required` from authenticated user APIs.
- A valid user session that requests another owner's resource receives the same not-found response as an unknown resource. Resource lookups do not reveal whether another owner has the guessed ID.
- `GET /health` remains public and returns only coarse readiness: `{"status":"ok"}` or `{"status":"degraded"}`.
- System `GET /diagnostics` is disabled with `404` in production. `GET /demos/{demo_id}/diagnostics` remains authenticated and owner-scoped.
- Render-worker manifest, media-upload, and result endpoints continue to use the separate `X-Render-Worker-Token` service credential. A browser session cannot replace it, and it does not grant user API access.
- Every browser-auth/session, demo, upload, replay, coaching, diagnostics, render-job, and private-media response is marked `Cache-Control: private, no-store` and varies on `Cookie, Origin`, including error responses. A shared edge cannot replay one owner's response to another owner before application authorization runs.
- Render-worker callback text and background exception strings are never persisted or projected to users. Failed render state exposes only `RENDER_FAILED` or `RENDER_WORKER_UNAVAILABLE` with stable safe copy; logs record the job ID and stable code, not callback-provided text, credentials, paths, or traceback content.

## Browser Authentication Flow

1. The frontend sends the browser to `GET /auth/login?return_to=<safe-relative-path>`.
2. The backend stores a short-lived, single-use login attempt in Redis and redirects to the configured OIDC authorization endpoint with `state`, `nonce`, and a PKCE S256 challenge.
3. The provider redirects to the server-only `OIDC_REDIRECT_URI` at `GET /auth/oidc/callback` with an authorization code and state. This API path is intentionally distinct from the frontend completion page.
4. The backend requires the state cookie to match, consumes the Redis login attempt, exchanges the code with its PKCE verifier, fetches the configured JWKS, and accepts only a valid token using the configured supported asymmetric allow-list (`RS256` and/or `ES256`) for the exact issuer and client audience. Required claims include `iss`, `sub`, `aud`, `iat`, and `exp`; nonce must match and timestamps allow only the configured bounded clock skew. Multi-audience tokens must identify this client through `azp`.
5. The backend derives the owner ID, creates a random opaque session token, stores only its hashable lookup and compact owner/expiry record in Redis, and sets the token in the configured `HttpOnly`, `SameSite=Lax` cookie. Production cookies are `Secure` and use path `/`.
6. The backend redirects only to `FRONTEND_PUBLIC_URL/auth/callback`, carrying the sanitized relative `return_to`. The redirect never carries the authorization code, state, provider token, raw subject, or session token.
7. The frontend callback checks `GET /auth/session` with credentials and then returns to the sanitized in-app path.
8. `POST /auth/logout` deletes the Redis session and clears the browser cookie. A copied old cookie stops working after logout; natural Redis expiry enforces session expiry.

Only relative application paths up to 2,048 characters are accepted for `return_to`; oversized values, absolute URLs, scheme-relative URLs, and malformed values fall back to `/dashboard`.

## Stable Owner Mapping

The verified `(issuer, subject)` pair maps deterministically to an opaque ID:

```text
owner_v1_<base64url(sha256(issuer + NUL + subject))>
```

The value is stable for the same issuer and subject, contains neither input in plaintext, and fits the existing `VARCHAR(64)` owner column. Stage 2 does not add a user table or a schema migration. Blank issuer or subject claims are rejected. Production backfill must never assign an ownerless row to `dev-user`.

## Authenticated User API Boundary

The same session-derived owner applies to the following surfaces:

- Demo Library list, status, rename, archive, and unarchive.
- Mock upload, real `.dem` upload, and parser retry.
- Replay, deterministic coaching, and demo diagnostics.
- Video status, manual development/QA upload, calibration, mock render, `render_clip`, and render-job list.
- Private video byte delivery.

User-facing responses must not expose `owner_id`, raw storage keys, local filesystem paths, issuer/subject claims, provider tokens, or session tokens. Worker-only contracts may continue to carry storage keys where the separate service boundary requires them.

## Private Video Delivery

The only browser media route is:

```text
GET  /demos/{demo_id}/media/video
HEAD /demos/{demo_id}/media/video
```

The video URL in user-facing replay/video metadata points to that demo-scoped route. There is no public `/media/videos` static mount and legacy static paths are not accepted by the frontend.

At byte-delivery time, the API:

1. Resolves the current owner from the opaque session.
2. Loads the demo through the owner-scoped query.
3. Requires ready video metadata and a local `videos/{demo_id}/...` storage key bound to the same demo.
4. Rejects cross-demo keys, traversal, symlinks, missing/non-regular files, and paths outside the configured video root.
5. Streams the file as `video/mp4` without an unbounded full-file read.

The route supports browser playback primitives:

- Full `GET` returns `200` and `Accept-Ranges: bytes`.
- A satisfiable single byte range returns `206` with correct `Content-Range` and `Content-Length`.
- An unsatisfiable range returns `416` without path details.
- `HEAD` returns the same metadata and no response body.
- Responses use `Cache-Control: private, no-store`, `Cross-Origin-Resource-Policy: same-origin`, `Vary: Cookie, Origin`, and `X-Content-Type-Options: nosniff`. Production also rejects browser media requests whose Fetch Metadata identifies a same-site sibling or cross-site initiator.
- Local media is opened through no-follow directory descriptors and streamed from the already-validated file descriptor. Parent-directory symlinks and check-then-reopen races cannot redirect a validated request to another artifact.

A guessed or copied URL is not a grant: the session and owner check run on every request, including range requests. Anonymous or expired sessions receive `401`; wrong-owner, missing, malformed, or unsafe artifacts receive the same generic `404 Media not found`. An archived demo remains directly accessible to its owner. If media is absent, rejected, or the session expires during playback, the 2D replay remains usable and the UI shows the existing media fallback.

## Frontend Session Contract

The frontend has one auth/session provider and one authenticated API transport:

- All JSON and multipart requests use `credentials: "include"`.
- All private browser/API responses disable shared or persistent caching and merge `Cookie, Origin` into `Vary` without dropping existing values.
- In production, `CORS_ORIGINS` must contain exactly the single `FRONTEND_PUBLIC_URL`. Unsafe cookie-authenticated methods require that exact `Origin`; missing, stale, sibling, or untrusted origins receive `403` and no mutation. Render-worker service routes remain outside this browser-session CSRF rule.
- Dashboard and Demo Detail share the same login boundary.
- `401` transitions an authenticated view to a clear session-expired state and offers sign-in again.
- Sign-in, callback, session check, and sign-out are minimal account surfaces; there is no password UI, provider token handling, or account/team management.
- The private `<video>` source is same-origin and uses credentials. It accepts only the demo-scoped private-media route and rejects legacy static or arbitrary absolute media URLs.
- No auth secret or token is stored in `localStorage`, embedded in `NEXT_PUBLIC_*`, or placed in a media URL.

## Production Configuration

All identity-provider values are server-side. Do not place secrets in `NEXT_PUBLIC_*`, commit them, or paste real values into checked-in env examples.

| Variable | Production requirement | Purpose |
| --- | --- | --- |
| `AUTH_MODE` | Must be `production` | Enables fail-closed API identity rules; the worker also requires an explicit mode. |
| `FRONTEND_PUBLIC_URL` | Exact HTTPS application origin; must equal `BACKEND_PUBLIC_URL` and be the sole value in `CORS_ORIGINS` | Trusted destination for the post-callback frontend redirect. |
| `BACKEND_PUBLIC_URL` | Exact HTTPS application origin with no path/query/fragment | API origin; `OIDC_REDIRECT_URI` must use this exact origin. |
| `OIDC_ISSUER` | Exact HTTPS issuer | Required `iss` value. |
| `OIDC_CLIENT_ID` | Non-empty | OIDC client and required audience. |
| `OIDC_CLIENT_SECRET` | Optional, server-side | Used only when the selected OIDC client type requires it. |
| `OIDC_ALLOWED_ALGORITHMS` | Non-empty subset of `RS256,ES256`; default `RS256,ES256` | Explicit asymmetric signature allow-list; symmetric/`none` algorithms are rejected. |
| `OIDC_AUTHORIZATION_ENDPOINT` | HTTPS URL | Authorization Code + PKCE start endpoint. |
| `OIDC_TOKEN_ENDPOINT` | HTTPS URL | Server-side code exchange endpoint. |
| `OIDC_JWKS_URL` | HTTPS URL | Trusted signing-key set. |
| `OIDC_REDIRECT_URI` | HTTPS backend `/auth/oidc/callback` URL | Exact registered server callback URL; it must not collide with the frontend `/auth/callback` page. |
| `AUTH_COOKIE_SECURE` | Must be enabled | Requires the session and state cookies to travel over HTTPS. |
| `AUTH_SESSION_COOKIE_NAME` | Defaults to `__Host-cs2_session`; production requires `__Host-` | Opaque browser session cookie. |
| `AUTH_STATE_COOKIE_NAME` | Defaults to `__Host-cs2_oidc_state`; production requires a distinct `__Host-` name | Short-lived login state cookie. |
| `AUTH_SESSION_TTL_SECONDS` | `1..86400`; default `3600` | Redis/browser session lifetime, capped by identity-token expiry. |
| `AUTH_LOGIN_TTL_SECONDS` | `1..600`; default `300` | Single-use OIDC login-attempt lifetime. |
| `AUTH_CLOCK_SKEW_SECONDS` | `0..300`; default `30` | Bounded timestamp validation leeway. |
| `REDIS_URL` | Required runtime dependency | Opaque sessions and one-time login attempts. |
| `CORS_ORIGINS` | Exactly one HTTPS origin equal to `FRONTEND_PUBLIC_URL`; no wildcard or additional stale/sibling origin | Credentialed browser API access. |
| `RENDER_WORKER_TOKEN` | Non-empty and not the development default | Separate render-worker service credential. |

`NEXT_PUBLIC_API_BASE_URL` remains a public API origin, not a secret. Split-origin local development uses the explicit development harness. Production requires one HTTPS origin routing frontend pages and API/auth/media paths, so `__Host-` cookies, callback redirects, CSRF origin checks, and native private-media requests share the same origin. The edge must send `/auth/oidc/callback`, `/auth/login`, `/auth/session`, and `/auth/logout` to FastAPI while preserving the frontend `/auth/callback` page in Next.js.

## Acceptance Matrix

Run the owner matrix with two independently authenticated identities A and B, plus anonymous, invalid, expired, and revoked sessions. Repeat every applicable action for both archived and active resources where that state changes behavior.

| Surface | Owner A | Owner B / guessed ID | Anonymous, invalid, expired, or revoked |
| --- | --- | --- | --- |
| Library list | Only A rows | Only B rows | `401` |
| Status, replay, coaching, demo diagnostics | A resource succeeds | Generic `404` | `401` |
| Rename, archive/unarchive, retry | A mutation succeeds | Generic `404`; no mutation | `401`; no mutation |
| Mock/real upload | New row owned by A | New row owned by B | `401`; no row/artifact/job |
| Video status/upload/calibration | A resource succeeds | Generic `404`; no mutation | `401`; no mutation |
| Mock/clip render and job list | A resource succeeds | Generic `404`; no job/mutation | `401`; no job/mutation |
| Private video GET/HEAD/Range | `200`/`206` as applicable | Generic `404`, no bytes/path details | `401`, no bytes/path details |

In production, also prove that `X-Dev-User-Id` cannot select an owner, missing/inconsistent OIDC or application-origin configuration stops startup, invalid issuer/audience/signature/algorithm/claims/nonce/timestamps are denied, unsafe-method requests from missing/untrusted origins cause no mutation, logout revokes the server session, `/diagnostics` is `404`, `/health` is coarse, and `/media/videos/...` is not mounted.

Also inspect successful and failure responses for every cookie-authenticated JSON surface: each must include `Cache-Control: private, no-store` and `Vary` containing both `Cookie` and `Origin`. Inject render-worker callback and worker exception strings containing a fake path, token, and traceback marker; the database, replay/video payload, render-job payload, and logs must contain only the stable safe code/copy.

## Stage Boundary and Deferred Work

Stage 2 intentionally keeps the local storage adapter and the existing Redis job-dispatch behavior. The following work remains ordered and separate:

The older `docs/deployment_target_decision_v1.md` and `docs/internal_preview_packaging_v1.md` remain development-preview history and tooling. Their no-production-auth, split-origin, or public `/media/videos` guidance must not be used for a production candidate; this document and the current deployment/RC documents are authoritative for the Stage 2 production boundary.

1. Stage 3: object storage and secure Artifact Intake.
2. Stage 4: reliable delivery, crash recovery, atomic claim, and idempotent execution.
3. Stage 5: isolated untrusted `.dem` parsing with CPU, memory, disk, and timeout limits.
4. Stage 6: formal migrations, CI/CD, observability, and backup/restore.
5. Stage 7: real demo corpus and invite-only beta.

No Stage 2 decision makes LLM coaching, real first-person GPU rendering, user-uploaded MP4 as the primary flow, billing, or team collaboration part of V1.
