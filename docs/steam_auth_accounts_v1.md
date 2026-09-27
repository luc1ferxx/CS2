# Steam Authentication and Accounts V1

This document defines the Phase 1 Steam-first identity boundary. It adds Steam login and formal account mapping without adding match-history authorization, sharing codes, match sync, or Demo download.

## Runtime shape

Production uses Steam OpenID 2.0 when `AUTH_PROVIDER=steam`. Production requires this provider choice explicitly so an older OIDC deployment cannot silently switch identity authority. Steam OpenID is not OAuth or OIDC: the browser is redirected to Valve's fixed OpenID provider and the backend verifies the positive assertion directly with Valve before it creates any account or session. The only identity claim accepted from that flow is a validated individual SteamID64.

The existing OIDC Authorization Code + PKCE implementation remains available only as a compatibility provider selected with `AUTH_PROVIDER=oidc`. Both providers resolve to the same account model, opaque Redis session, `owner_id` authorization boundary, CSRF origin checks, and private-response cache policy. There is no second browser session system.

The primary routes are:

- `GET /auth/steam/login`: creates a single-use Redis login attempt and redirects to Steam.
- `GET /auth/steam/callback`: validates and directly verifies the assertion, resolves the account, rotates any prior session, and redirects to the frontend callback.
- `GET /auth/me`: returns only compact display metadata for the authenticated account. It never exposes `owner_id`. For a Steam account it also returns `account.steamId`, the signed-in viewer's own SteamID64, so Demo Detail can default the reviewed player to them; it is never another account's ID. Development and OIDC accounts omit it.
- `POST /auth/logout`: revokes the Redis session and expires the session cookie.
- `DELETE /auth/account`: production only. Deletes the signed-in account and all of its data, and ends every session of that account on every device (see [Account deletion](#account-deletion)).

`GET /auth/login`, `GET /auth/oidc/callback`, and `GET /auth/session` remain compatibility routes. They are not a second enabled provider when `AUTH_PROVIDER=steam`.

## Steam OpenID validation

The backend derives the exact realm and callback from trusted `BACKEND_PUBLIC_URL`; it does not trust request `Host` or accept a configurable OpenID endpoint. Login attempts use high-entropy state in both a short-lived `Secure`, `HttpOnly`, `SameSite=Lax` cookie and a single-use Redis record.

The callback fails closed unless all of these checks pass:

- callback parameters are unique and bounded;
- cookie state and query state match, and the Redis attempt can be atomically consumed;
- namespace, mode, pinned provider endpoint, exact `return_to`, and stored realm/callback agree;
- `claimed_id` and `identity` are identical canonical Steam identifiers containing a valid individual SteamID64;
- bounded, redirect-free XRDS discovery of that exact claimed ID identifies the OpenID 2.0 sign-on type and pinned Valve endpoint;
- the required OpenID fields are listed in `openid.signed` and present;
- `response_nonce` is well formed, fresh, and not already reserved in Redis;
- a direct `check_authentication` POST to the pinned Valve endpoint returns `is_valid:true` in the OpenID key-value response.

In production the verified SteamID64 must then be in `STEAM_LOGIN_ALLOWLIST` (unless it is `*`) before any account is resolved; otherwise the callback revokes any prior session, clears the state and session cookies, and redirects to `/auth/callback?error=not_invited` without creating an account, external identity, or session.

The callback query, assertion signature, state, opaque session, and Steam Web API key are not application-log or frontend payload data. The application installs a Uvicorn access-log filter for Steam/OIDC callback queries and returns `Referrer-Policy: no-referrer`; ingress/CDN logs must also redact or suppress callback query strings, and outbound Steam debug logging must remain disabled. Redis fixed-window limits bound Steam login and callback requests per resolved client address; the edge should still enforce broader abuse controls. Production requires HTTPS, `__Host-` cookies, one exact frontend/API origin, exact CORS, Redis, and all existing storage/render-worker production controls.

## Accounts and identity mapping

Migration `2026071901_create_accounts_and_external_identities` creates:

- `accounts(owner_id, display_name, avatar_url, created_at, updated_at)`;
- `external_identities(id, provider, subject, owner_id, created_at, last_login_at)`.

The schema enforces unique `(provider, subject)` and unique `(owner_id, provider)`. A verified Steam identity therefore belongs to exactly one account, and one account cannot silently acquire two different identities from the same provider. Steam profile names and avatars are display metadata only; they are never used to merge accounts.

The migration is tracked in `app_schema_migrations` with a checksum and fails closed for unknown versions, changed checksums, or untracked account tables. PostgreSQL startup serializes API/worker schema upgrades with an advisory lock. Existing demo owner strings are preserved, and `demos.owner_id` intentionally does not gain an account foreign key so explicit development/test owners and legacy local data remain valid. Deleting an `accounts` row therefore cascades to nothing the owner uploaded; account deletion removes demos, verdicts, and Steam rows explicitly.

Phase 1 has no implicit account linking. A future multi-identity binding flow must require an authenticated account plus recent authentication of the new identity. If `(provider, subject)` already belongs to another owner, it must return a conflict and must never reassign or merge by display data.

## Profile enrichment

`STEAM_WEB_API_KEY` is server-only. Production requires it and refuses to start without a real key; development and test may leave it unset. The same key also authorizes the optional match-history calls (`docs/steam_match_sync_v1.md`).

With the key configured, every sign-in calls the official `ISteamUser/GetPlayerSummaries/v2` endpoint with the verified SteamID64:
- The call happens before the allowlist check. An uninvited account's SteamID64 is therefore sent to Steam once, but nothing is stored for it.
- The backend keeps only the persona name (normalized, at most 128 characters) and the avatar URL, and only when that URL is HTTPS on a Steam CDN host. Both are refreshed on every sign-in.
- Profile URL, real name, country, and the rest of the response are discarded.
- Private profiles, timeouts, rate limits, malformed responses, or a mismatched SteamID do not fail login; the UI falls back to `Steam account`.

Sign-in never asks for or stores a Steam password, Steam Guard code, or Steam browser cookie. The optional Steam match-history link is a separate, user-initiated step (`docs/steam_match_sync_v1.md`). When a user connects it, the Game Authentication Code and Match Sharing Codes they submit, plus the sharing codes discovered later, **are** stored, AES-256-GCM encrypted, in `steam_connections` and `steam_matches`. Disconnecting deletes them, and so does deleting the account.

## Account deletion

`DELETE /auth/account` with the JSON body `{"confirm": "delete-my-account"}`:

| Case | Response |
| --- | --- |
| Deleted | `204`. The response expires `__Host-cs2_session` with exactly the attributes logout uses. |
| Missing or wrong confirmation | `400` `confirmation_required` |
| No session | `401` |
| `AUTH_MODE=development` or `test` | `409` `account_deletion_unavailable`. The local development owner has no account row, and one click must never wipe the local library; matches can still be deleted one by one. |

In production the request passes the same session and exact-`Origin` checks as every other unsafe method. Caddy routes `/auth/account` to the API; `/account` is the Next.js "账户与数据" page.

**What is deleted**
- Every demo of the owner, through the single-match deletion path: rows, verdicts, and stored artifacts.
- `coaching_feedback`, `steam_matches`, `steam_connections`, `external_identities`, and `accounts`.
- Storage is purged after commit and retried through a durable outbox.

**Sessions**
- New session records carry `issuedAt` (milliseconds).
- Deletion writes the owner revocation marker `auth:owner-revoked:{sha256(owner_id)}` before the database transaction commits. Its value is the current time in milliseconds, and its TTL is 86400 + 300 seconds.
- Session resolution rejects and deletes any session of that owner whose `issuedAt` is missing or not later than the marker. Other devices therefore get `401` immediately, not after the session TTL.

**Concurrent uploads and imports**: the upload commit and the Steam import commit re-check the account row inside their transaction. After deletion, a racing upload returns `401` `account_deleted` and leaves no row or object.

**Signing in again**: the Steam identity mapping is gone, so a later sign-in with the same SteamID64 (if it is still on the allowlist) creates a new, empty account with a new random `owner_id`. To keep someone out, remove them from `STEAM_LOGIN_ALLOWLIST`.

The full protocol, the retention of deleted data in backups and logs, and the test matrix are in [data_deletion_v1](data_deletion_v1.md).

## Configuration

| Variable | Default | Contract |
| --- | --- | --- |
| `AUTH_MODE` | unset | Must be explicit: `development`, `test`, or `production`. |
| `AUTH_PROVIDER` | unset | Required explicitly in production: `steam` or legacy-compatible `oidc`. Local Compose selects `steam`. |
| `BACKEND_PUBLIC_URL` | `http://localhost:8000` | Trusted origin used to derive Steam realm and `/auth/steam/callback`; exact HTTPS origin in production. |
| `FRONTEND_PUBLIC_URL` | `http://localhost:3000` | Trusted post-login origin; exact same HTTPS origin as the API in production. |
| `AUTH_COOKIE_SECURE` | `false` | Must be true in production. |
| `AUTH_SESSION_COOKIE_NAME` | `__Host-cs2_session` | Opaque session cookie; `__Host-` required in production. |
| `STEAM_AUTH_STATE_COOKIE_NAME` | `__Host-cs2_steam_state` | Single-use Steam state cookie; distinct `__Host-` name required in production. |
| `AUTH_SESSION_TTL_SECONDS` | `3600` | Redis/browser session lifetime. |
| `AUTH_LOGIN_TTL_SECONDS` | `300` | Single-use login-attempt lifetime. |
| `AUTH_CLOCK_SKEW_SECONDS` | `30` | Allowed future clock skew for the OpenID nonce. |
| `STEAM_OPENID_NONCE_TTL_SECONDS` | `600` | Assertion freshness and replay-reservation window; must cover login TTL plus skew. |
| `STEAM_WEB_API_KEY` | unset | 32-character server-side key for profile enrichment and the optional match-history calls. Required in production; optional in development/test. |
| `STEAM_LOGIN_ALLOWLIST` | unset | Invite gate: comma-separated individual SteamID64s (at most 1000, unique), or `*` for every Steam account. Required in production with `AUTH_PROVIDER=steam`; development/test validate the format but never enforce it. |
| `NEXT_PUBLIC_AUTH_PROVIDER` | `steam` | Public frontend provider selector; must equal `AUTH_PROVIDER`, contains no credential. |

OIDC variables are required only when `AUTH_PROVIDER=oidc`. `NEXT_PUBLIC_AUTH_PROVIDER` is a non-secret UI selector; no authentication credential may use a `NEXT_PUBLIC_` name or be passed to frontend, queue worker, or render-worker containers.

## Development compatibility and non-goals

`AUTH_MODE=development` and `AUTH_MODE=test` retain the explicit `DEV_USER_ID` / `X-Dev-User-Id` harness. Production never falls back to that header or value. Development `/auth/me` reports a local account label without requiring an account row; for the same reason `DELETE /auth/account` answers `409` there.

This phase does not add Steam match credentials, match-history discovery, scheduled sync, Demo CDN access, Demo source providers, parser/replay/coaching changes, or Steam/CS2 automation. Real Steam callback interoperability, public-provider availability, and profile enrichment still require deployment smoke with an HTTPS registered origin; unit tests use controlled provider responses.
