# Steam Demo Import Adapter V1

This phase connects an owner-scoped discovered Steam match to the existing safe
`.dem` intake and parser pipeline. It does not establish a public Valve Demo
download API. The shipped provider is therefore `disabled`: importing a match
returns a fixed unavailable result and keeps manual `.dem` upload available.

## Provider and licensing boundary

Valve's documented `ICSGOPlayers_730/GetNextMatchSharingCode/v1` response
contains the next Match Sharing Code, not a Demo URL, map, score, player list, or
match timestamp. The Steam Web API overview describes publisher and partner
access generally, but does not document a public personal-match Demo endpoint.
The Steam Web API terms also do not grant permission to infer an unpublished
replay-CDN or Game Coordinator contract.

Official references:

- <https://developer.valvesoftware.com/wiki/Counter-Strike:_Global_Offensive_Access_Match_History>
- <https://partner.steamgames.com/doc/webapi_overview>
- <https://steamcommunity.com/dev/apiterms>

`DemoSourceProvider` is intentionally pluggable, but a real provider may be
registered only after Valve exposes an explicitly authorized partner endpoint
or the project obtains a formal data-provider license that covers the target
matches and `.dem` delivery. A provider adapter, its exact host allowlist, its
credential handling, and its retention terms require a separate review.

This build accepts only:

```text
STEAM_DEMO_PROVIDER=disabled
STEAM_DEMO_EXPERIMENTAL_REPLAY_CDN_ENABLED=0
```

Any blank/unknown provider or enabled experimental replay-CDN switch fails API
startup in development, test, and production. Community share-code decoding,
Steam private-page scraping, Game Coordinator emulation, and undocumented CDN
URLs are not implemented. Test-only fake providers never enter runtime config.

## Import and ownership flow

`POST /steam/matches/{id}/import` resolves the row with both its opaque id and
the authenticated `owner_id`. A missing or foreign id returns the same generic
`404` before decrypting the stored sharing code or contacting any provider.
An atomic run id plus expiring lease prevents two API processes from attaching
two Demos to the same match; `steam_matches.demo_id` is unique.

The intended licensed-provider flow is:

```text
owner-scoped Steam match
  -> decrypt one Match Sharing Code in API memory
  -> licensed DemoSourceProvider returns one HTTPS source descriptor
  -> secure downloader writes a private seekable scratch file
  -> existing quarantine and SHA-256/type/size validation
  -> accepted source promotion
  -> one owner-scoped Demo + real_parse job + Steam match binding transaction
  -> existing Redis parser queue
  -> durable job-specific marker after successful queue submission
  -> ready Demo Detail / 2D review only after parser success
```

The downloader must never write directly to upload storage or create a parser
job from network bytes. It hands the complete private scratch stream to the same
`ArtifactIntakeService` used by manual `.dem` upload. Intake verifies the actual
byte count, SHA-256, extension and content type, and rejects known archive or
executable prefixes before accepted promotion. It does not positively prove a
CS2 Demo magic value; semantic validity remains the parser's responsibility.

Match states are truthful:

- `discovered`: match history found a sharing code; no Demo is attached.
- `demo_pending`: one active import claimant owns the short database lease before transfer starts.
- `downloading`: a reviewed provider returned a source and bounded transfer began.
- `parsing`: accepted source and parser job are bound to the match.
- `ready`: the real `.dem` parsed successfully; the existing Demo Detail link is valid.
- `unavailable`: provider, download, intake, dispatch, or parser failed safely.

Map, duration, side-round wins, and a compact player list stay absent until
parser success. The system does not infer them from a sharing code or provider
filename, and it does not fabricate match time or unsupported team-score data.

Disconnect deletes the encrypted authorization, cursor, connection, and Steam
match discovery/import rows and stops future sync/import. An accepted Demo is an
independently owner-scoped library artifact: disconnect does not delete it. It
continues to follow the existing archive and artifact-retention lifecycle.

## Download boundary for a future licensed provider

Only raw, uncompressed `.dem` responses are accepted. `Content-Encoding`,
archive/compressed content types, and ambiguous or duplicate critical headers
are rejected before body consumption. This makes the accepted transfer's
compression ratio exactly 1:1; archive extraction and compression bombs are
outside the downloader contract.

Every source and redirect hop must satisfy all of these rules:

- HTTPS only, default port only, no userinfo, fragment, IP-literal URL, or unsafe
  hostname syntax.
- Exact lowercase DNS hostname membership; no wildcard or suffix matching.
- Resolve every hop and reject loopback, private, link-local, multicast,
  unspecified, reserved, carrier-grade NAT, and metadata destinations.
- Pin the actual TCP connection to one validated numeric address while using the
  original allowlisted hostname for TLS SNI, certificate verification, and Host.
- Revalidate redirects independently; never forward provider credentials to a
  different host.
- Enforce configured redirect, connection/read/total timeout, byte-size,
  per-owner concurrency, and global concurrency limits.
- Use a private `0700` temporary directory and `0600`, exclusive, no-follow
  scratch file; clean it on success, rejection, interruption, and timeout.

Concurrency is coordinated through expiring Redis leases and fails closed when
the limiter is unavailable. The per-owner limit cannot exceed the global limit,
the lease lifetime must cover the configured total transfer timeout, and Redis
server time—not an API host clock—controls lease expiry.

The database parser job is durable, but Redis delivery is best-effort. A
job-specific marker distinguishes the immediate database-to-Redis crash window;
if a marked job is still queued after 30 seconds, it also becomes requeueable so
a Redis restart cannot hide it forever. Repeating the import endpoint resubmits
only that recoverable queued job, and the worker's queued-to-processing
compare-and-set makes duplicate delivery harmless. An expired import run lease
similarly exposes only an `import_retryable` boolean, never the run id or lease.

This is focused manual recovery, not a general durable queue or automatic
reconciler. A hard API process loss after accepted promotion but before the Demo
binding transaction can also leave an unbound accepted object; in-process
failures are deleted, while accepted-object orphan enumeration/GC remains an
explicit storage-lifecycle limitation.

## Runtime configuration

These values belong only in the backend API runtime. They are not frontend
configuration and are intentionally absent from the parser/render worker:

| Variable | Default | Validation |
| --- | --- | --- |
| `STEAM_DEMO_PROVIDER` | `disabled` | only `disabled` is registered in this build |
| `STEAM_DEMO_EXPERIMENTAL_REPLAY_CDN_ENABLED` | `false` | `true` always fails startup |
| `STEAM_DEMO_DOWNLOAD_ALLOWED_HOSTS` | empty | at most 16 unique exact lowercase DNS hosts; no wildcard, port, or IP literal |
| `STEAM_DEMO_DOWNLOAD_MAX_BYTES` | `536870912` | 16 bytes through 1 GiB |
| `STEAM_DEMO_DOWNLOAD_MAX_REDIRECTS` | `3` | 0 through 3 |
| `STEAM_DEMO_DOWNLOAD_CONNECT_TIMEOUT_SECONDS` | `5` | 0.1 through 30 seconds |
| `STEAM_DEMO_DOWNLOAD_READ_TIMEOUT_SECONDS` | `10` | 0.1 through 30 seconds |
| `STEAM_DEMO_DOWNLOAD_TOTAL_TIMEOUT_SECONDS` | `60` | covers connect/read timeout; at most 120 seconds |
| `STEAM_DEMO_DOWNLOAD_GLOBAL_CONCURRENCY` | `4` | 1 through 32 |
| `STEAM_DEMO_DOWNLOAD_OWNER_CONCURRENCY` | `1` | 1 through global concurrency |
| `STEAM_DEMO_DOWNLOAD_CONCURRENCY_LEASE_SECONDS` | `120` | covers total timeout; at most 3600 seconds |

Setting an allowlist alone cannot enable network access. A future licensed
provider still requires an explicit code registration and an update to the
supported-provider startup allowlist. Provider credentials must use server-side
secret injection, must not use `NEXT_PUBLIC_*`, and must never be logged,
returned, committed, or sent to the parser/render worker.

## Verification boundary

Automated coverage must include disabled/default provider behavior, unsupported
provider and experimental-switch startup rejection, exact-host validation,
download/redirect/DNS/TLS/size/timeout/interruption rejection, cleanup,
idempotent concurrent import, expired import lease recovery, stale/lost Redis
delivery recovery, parser failure, disconnect during import or parse, and owner
A/B isolation. Test providers and transports use synthetic bytes only.

No live automatic Demo smoke is possible until a provider contract and
credential have been formally approved. That is an external integration limit,
not a reason to request a user's Steam password, Steam Guard code, browser
cookie, or personal account credentials. Manual upload remains the reliable
end-to-end path for an approved local `.dem` sample.
