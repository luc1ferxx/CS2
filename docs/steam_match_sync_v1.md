# Steam Match History Sync V1

This phase adds owner-scoped Steam match discovery after Steam OpenID sign-in. It
does not download a Demo, decode sharing codes, scrape Steam pages, contact the
Game Coordinator, or change the parser/replay/coaching/render contracts.

## Trust and data flow

1. Steam OpenID verifies one SteamID64 and maps it to an opaque account
   `owner_id` through `external_identities`.
2. The connection form accepts only a Game Authentication Code and one existing
   Match Sharing Code. It never accepts SteamID64, password, Steam Guard, a
   publisher Web API key, or a Demo URL.
3. The API resolves SteamID64 from the authenticated owner's `steam` external
   identity. The server-only publisher `STEAM_WEB_API_KEY` calls Valve's fixed
   `ICSGOPlayers_730/GetNextMatchSharingCode/v1` HTTPS endpoint.
4. Every HTTP 200 response stores one encrypted sharing code and atomically
   advances the encrypted known-code cursor. HTTP 202 marks the connection
   caught up. One request consumes at most 20 HTTP 200 results.
5. `GET /steam/matches` exposes only an opaque row id, source, discovery time,
   and lifecycle status. It never returns SteamID64, sharing codes, fingerprints,
   encryption metadata, or credentials.

Valve's endpoint returns only the next sharing code. It does not return map,
score, match time, players, or a supported Demo download URL. The Dashboard must
therefore keep those values absent until a real `.dem` has passed the existing
parser. The separately reviewed import boundary and its disabled-by-default
provider contract are documented in `docs/steam_demo_import_v1.md`.

Official references:

- <https://developer.valvesoftware.com/wiki/Counter-Strike:_Global_Offensive_Access_Match_History>
- <https://partner.steamgames.com/doc/webapi_overview>
- <https://steamcommunity.com/dev/apiterms>

## Database ownership and idempotency

`steam_connections` has one row per formal account owner. It stores the bound
SteamID64, two AES-GCM ciphertexts (Game Authentication Code and current known
code), independent nonces, encryption key version, sync lease token, status,
bounded retry state, timestamps, and safe error metadata.

`steam_matches` belongs to both the same owner and connection. The sharing code
is AES-GCM encrypted; a one-way fingerprint supports the unique
`(owner_id, share_code_hash)` idempotency constraint. All service queries include
`owner_id`; a composite `(connection_id, owner_id)` foreign key also prevents a
match row from referencing another owner's connection. Deleting a connection
cascades to its discovered match rows.

The lifecycle enum is deliberately wider than this phase's producer:

- `discovered`: official history returned a sharing code; no Demo is attached.
- `demo_pending`, `downloading`, `parsing`: reserved for an explicitly reviewed
  Demo source/import adapter.
- `ready`: a real `.dem` has parsed successfully and may link to Demo Detail.
- `unavailable`: a reviewed provider could not supply a usable Demo.

The history-sync service creates only `discovered`. A later import request may
advance the row through the reserved states, but the shipped disabled provider
does not pretend that sharing-code discovery is Demo availability.

## Credential encryption

`STEAM_CREDENTIAL_ENCRYPTION_KEY` is a URL-safe base64 encoding of exactly 32
random bytes. `SteamCredentialCipher` uses AES-256-GCM with a fresh 96-bit nonce
for every value. Additional authenticated data binds ciphertext to the owner,
purpose, and record id, preventing a ciphertext from being moved between owners,
columns, connections, or matches. The stored key version fails closed when it
does not match the configured active version.

Production rejects a missing, malformed, or checked-in development key. V1 has
one active key rather than an online keyring: rotation requires a separately
reviewed offline re-encryption migration while the old key is still configured.
Changing the active key without that migration intentionally makes old
credentials unusable instead of silently weakening encryption.

Credentials, known codes, next codes, SteamID64, publisher key, ciphertext,
nonce, and fingerprints are absent from API payloads. The client clears form
state after submission and does not use browser storage. Outbound request-level
`httpx`/`httpcore` logging is suppressed because Valve's GET contract places user
authorization values in the query string. Application errors store and expose
only fixed safe codes/messages. Credential JSON is limited to 4 KiB before
validation, rejects extra fields, and validation failures never reflect input.

## Sync state machine

| Valve result | Local action |
| --- | --- |
| `200` + valid `result.nextcode` | idempotently insert `discovered`, advance cursor, continue until 20 |
| `202` + `result.nextcode = n/a` | stop and mark `caught_up` |
| `403` | stop, mark `authorization_required`, request a new Game Authentication Code |
| `412` | stop, mark `authorization_required`, request a valid initial sharing code |
| `429` | stop, persist exponential backoff and `next_retry_at` |
| `503` or network timeout | stop, persist exponential backoff and `next_retry_at` |
| malformed/inconsistent response | stop and fail closed with a safe protocol error |

The API never sleeps and retries inside a browser request. Exponential retry
state is persisted, `Retry-After` is bounded when present, and a short database
lease uses an atomic conditional update so SQLite and PostgreSQL both prevent
overlapping Sync-now runs. A stale lease can be replaced; the per-run token
prevents the older runner from advancing the cursor afterward. Valve responses
are streamed and rejected after 16 KiB of decompressed data.

Redis applies a fixed manual-sync invocation budget of 3 requests per owner and
30 requests globally per 60-second window before any credential is decrypted or
Valve is called. Redis failure is fail-closed. A Valve 429 also installs a
best-effort global publisher-key breaker for the persisted retry interval so one
owner's rate-limit signal protects the shared server credential.

Only manual `POST /steam/sync` exists. `STEAM_SCHEDULED_SYNC_ENABLED` is a
separate fail-closed switch and must remain false in V1 because no scheduler or
separate Steam queue/consumer is implemented. The existing Redis parser/render
worker protocol remains unchanged and receives no Steam credential or browser
session data.

## API

- `GET /steam/connection`: safe connection, retry, and repair state.
- `POST /steam/connection/credentials`: replace encrypted authorization and
  reset the cursor from the supplied initial Match Sharing Code.
- `DELETE /steam/connection`: revoke local access by deleting credentials,
  cursor, connection state, and discovered match rows.
- `POST /steam/sync`: enumerate at most 20 new sharing codes.
- `GET /steam/matches?limit=50`: list owner-scoped discovery rows.

Production unsafe requests continue requiring the authenticated opaque session
and exact trusted `Origin`. All `/steam/*` responses use `private, no-store` and
vary by Cookie/Origin.

Disconnecting does not delete independently owned Demo artifacts. Deleting a
connection removes Steam authorization/discovery/import rows and stops future
sync/import; an already accepted owner-scoped `.dem` remains in the Demo Library
and follows the existing archive/storage lifecycle.

## Runtime configuration

- `STEAM_WEB_API_KEY`: server-only publisher key; required and format-validated
  in production.
- `STEAM_CREDENTIAL_ENCRYPTION_KEY`: URL-safe base64 AES-256 key.
- `STEAM_CREDENTIAL_ENCRYPTION_KEY_VERSION`: bounded identifier stored beside
  ciphertext, development default `dev-v1`; production should set its own version.
- `STEAM_SYNC_MAX_MATCHES`: hard upper bound of 20.
- `STEAM_SYNC_TIMEOUT_SECONDS`: bounded per-request Valve timeout.
- `STEAM_SYNC_RETRY_BASE_SECONDS` / `STEAM_SYNC_RETRY_MAX_SECONDS`: bounded
  persistent exponential backoff.
- `STEAM_SCHEDULED_SYNC_ENABLED`: must remain `false` for V1.

No real keys, codes, Steam identifiers, cookies, or ciphertext belong in source,
fixtures, screenshots, shell history, or checked-in environment files.
