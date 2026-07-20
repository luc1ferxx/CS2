# Object Storage and Safe Artifact Intake V1

This document freezes the implemented Stage 3 storage and intake contract for the rules-based 2D public beta. Steam-first Phase 1 later added a focused account/external-identity migration without changing this artifact contract. Reliable queue recovery, parser sandboxing, full legacy-schema migration, CI/CD, production observability, backup/restore, a real demo corpus, LLM coaching, and GPU rendering remain outside this document.

## Decision

Application code uses a provider-neutral `ArtifactStore` boundary and backend-independent logical artifact references. Development and tests may use the local adapter; production must select the private S3-compatible object adapter. The adapter is configured by protocol-level endpoint, region, bucket, and prefix settings rather than a named cloud vendor. No bucket, identity, credential, or other cloud resource is created by this stage.

New logical references are server generated and bind all of:

- opaque owner ID;
- demo ID;
- artifact kind;
- lifecycle state where relevant;
- a server-generated artifact or intake ID.

They never contain a client-controlled path. Bucket names, physical object keys, provider URLs, credentials, local paths, and object-store errors remain internal. Legacy `local://` references remain a development/read-compatibility boundary only; they are not accepted as proof that a new source artifact passed intake.

## Measurable Policy

The following values are versioned as `artifact_intake_v1`:

| Policy | Value | Required behavior |
| --- | --- | --- |
| Public source type | `.dem` only | Public `/uploads/demo` rejects `.zip` and every other extension before writing an artifact. Archive parsing remains development-test compatibility only and has no public route. |
| Maximum source bytes | 1 GiB (`1,073,741,824`) | The actual streamed byte count is authoritative. The first byte beyond the limit aborts the write and removes the incomplete quarantine object. |
| Minimum source bytes | 16 | This is the minimum already enforced by the current parser. Smaller input is `INTAKE_TRUNCATED`; Stage 3 does not invent an unverified CS2 magic signature. |
| Source stream chunk | 1 MiB | Validation and SHA-256 calculation never loads the complete upload into memory. The S3 adapter hashes the seekable private upload, rewinds it, and sends that stream directly; it rejects non-seekable inputs instead of creating another whole-object spool. |
| Multipart request allowance | Artifact limit plus 8 MiB envelope | Production session/origin or render-worker token checks reject unauthorized requests before consuming body bytes. Request accounting then rejects declared or actually received multipart bodies above the route envelope. A missing or dishonest `Content-Length` never relaxes the actual-byte limit. One multipart artifact upload is admitted per API process at a time. |
| Render result JSON allowance | 64 KiB | Worker token validation runs before body consumption; declared or actual callback JSON above the fixed envelope is rejected before JSON parsing. |
| Integrity algorithm | SHA-256 | The digest and exact byte count are calculated while writing quarantine, stored in accepted metadata, and rechecked during promotion/materialization. |
| Quarantine TTL | 1 hour | Quarantine older than the cutoff is abandoned and eligible for deterministic cleanup. Tests inject a clock and never sleep. |
| Incomplete/rejected retention | Conditional immediate deletion | Stream, validation, promotion, or metadata failure attempts generation-bound deletion. Failed quarantine deletion is covered by the 1-hour cleanup policy. An accepted residual is never consumable without a matching DB/job binding and requires later reconciliation if storage is unavailable during cleanup. |
| Accepted source retention | While the demo record requires retry/review | Stage 3 does not implement backup or a billing retention product. A DB-confirmed failed bind deletes the accepted orphan. An uncertain DB commit outcome preserves the object to avoid breaking a possibly committed row and defers reconciliation. |
| Parser materialization | At most the accepted size and never above 1 GiB | Materialization uses a private temporary directory, verifies version/size/SHA-256, and cleans on success, failure, and cancellation. This compatibility adapter is not a Stage 5 parser sandbox. |
| Replay JSON read/write bound | 128 MiB | Replay writes are atomic/versioned; oversized or malformed replay objects fail safely rather than producing an unbounded read. |
| Video source | Development/QA or render-worker only | Existing 2 GiB stream limit remains. Video objects are private, owner/demo bound, and never returned as public object URLs. User MP4 is not a primary V1 flow. |

Client `filename`, `Content-Type`, and `Content-Length` are untrusted hints. The server normalizes the display filename but generates the physical/logical key. A public source must have a `.dem` display extension. An explicit archive/executable media type or a standard incompatible container prefix such as ZIP content under a `.dem` name is rejected as `INTAKE_CONTENT_MISMATCH`.

Stage 3 deliberately distinguishes two corruption classes:

- Intake-detectable corruption, including zero/truncated input, size inconsistency, incompatible type/content, incomplete streaming, checksum mismatch, and failed promotion, is rejected before dispatch.
- Semantic `.dem` corruption that cannot be established without the real parser is accepted only after the byte-level policy, then fails through the existing safe `INVALID_DEMO` parser result. The API does not run `demoparser2`; Stage 5 will isolate that hostile parser workload.

## Artifact Lifecycle

```text
untrusted request
  -> bounded private quarantine write
  -> exact size + SHA-256 + safe type/content policy
  -> verified promotion to immutable accepted reference
  -> accepted metadata bound to owner/demo/source
  -> Demo + real_parse job DB commit
  -> parser dispatch
```

Lifecycle states:

1. `quarantine`: private, non-deliverable, non-retryable, and never parser-readable.
2. `accepted`: promotion completed and `head` metadata matches owner, demo, kind, size, SHA-256, and policy version.
3. `rejected`: no accepted object exists; any remaining quarantine copy is cleanup-only.
4. `abandoned`: quarantine exceeded one hour without promotion.
5. `cleaned`: object and private metadata are absent.

Promotion copies or atomically moves a complete quarantine generation into the accepted location, verifies the resulting metadata/generation, and only then deletes quarantine. The S3 adapter uses a generation-bound server-side copy with destination `IfNoneMatch="*"`; it never downloads the whole object for promotion. A partial or unverifiable accepted target is conditionally deleted and cannot be consumed. Repeated cleanup and promotion cleanup are safe, but Stage 3 does not claim Stage 4 job idempotency.

## Accepted Source Binding

No schema migration is introduced. The existing fields are used as follows:

- `Demo.source_storage_key` points only to the promoted accepted source.
- The initial `real_parse` job stores a compact `sourceArtifact` snapshot in `metadata_json`: policy version, state, logical reference, size, SHA-256, accepted timestamp, and immutable store version/ETag.
- Parser claim and owner-scoped retry revalidate the job snapshot against object metadata and the demo owner/demo binding.
- Retry copies the same accepted snapshot into the new job. Missing metadata, a legacy fallback, wrong owner/demo/kind/state, size/digest drift, or a missing generation fails closed and creates no job or Redis payload.
- Mock parsing has no source artifact and is not forced through real-source intake.
- Queue payload processing requires `job.demo_id == demo.id` before any artifact is opened.

The accepted snapshot is compact metadata, not uploaded content, a path, a provider key, or a secret. User-facing list/status/diagnostics responses continue to expose only safe ingestion state and booleans.

## Provider-Neutral Storage Contract

The storage boundary owns:

- logical reference generation and strict parsing;
- private streamed write plus exact metadata;
- `head`, bounded read/range read, atomic JSON write, delete, promote, and abandoned-quarantine iteration;
- owner/demo/kind/state binding checks;
- conditional/version-bound reads;
- context-managed source materialization;
- backend-neutral readiness without exposing paths or provider configuration.

The local adapter uses no-follow directory/file descriptors and atomic temporary-file replacement for write, read, promotion, and delete operations. It must reject leaf and parent symlinks and check/reopen races.

The object adapter maps the same logical reference to a private bucket prefix. It does not set public ACLs or generate browser URLs. Reads bind the previously validated version ID or ETag so a HEAD-to-GET replacement cannot substitute another generation. The S3-compatible protocol is an adapter choice, not a cloud-vendor decision.

The object adapter requires the conditional-write model provided by pinned `boto3==1.43.49`: `PutObject.IfNoneMatch`, `CopyObject.IfNoneMatch`, and a version ID or `CopySourceIfMatch`. Runtime initialization rejects an SDK model missing those operations. Before a production endpoint is selected, the same contract suite must pass against that S3-compatible service; unsupported conditional-copy semantics are a deployment blocker, not a silent fallback.

## Safe Failure Contract

Intake exposes only stable codes and safe copy:

| Code | Meaning |
| --- | --- |
| `INTAKE_TYPE_REJECTED` | Public input does not have a `.dem` display extension. |
| `INTAKE_EMPTY` | Actual stream contains no bytes. |
| `INTAKE_TRUNCATED` | Actual source is below the frozen 16-byte minimum. |
| `INTAKE_TOO_LARGE` | Actual source or multipart envelope exceeds its configured bound. |
| `INTAKE_CONTENT_MISMATCH` | Extension/type/content facts are incompatible. |
| `INTAKE_INTEGRITY_FAILED` | Size, digest, version, or promotion verification differs. |
| `INTAKE_STORAGE_UNAVAILABLE` | Quarantine, promotion, read, or cleanup storage operation failed safely. |
| `INTAKE_REJECTED` | Policy rejected the upload without a more specific public category. |

Provider exceptions, endpoint/bucket names, object keys, credentials, local/Windows paths, traceback text, uploaded bytes, and raw parser data are not persisted, logged, or returned. Browser-private success and failure responses retain `Cache-Control: private, no-store` and `Vary: Cookie, Origin`.

## Private Delivery

Stage 2 remains authoritative:

- Browser media uses only owner-scoped `/demos/{demo_id}/media/video`.
- Every GET, HEAD, and Range request rechecks session, owner, demo, artifact binding, and immutable generation.
- Another owner, guessed ID, malformed reference, missing object, failed conditional read, or storage denial returns the same generic `404` without bytes or metadata.
- Anonymous, expired, and revoked sessions receive `401`.
- Unsafe production multipart requests with no valid session, wrong origin, or wrong render-worker token are rejected before multipart parsing or temporary-file spooling.
- Local reads retain no-follow FD behavior; object reads use conditional/version-bound private streaming.
- No `/media/videos` public mount, bucket URL, presigned browser URL, or public enumeration is introduced.
- Media failure leaves 2D replay and deterministic coaching usable.

Replay and video writes use the same adapter and owner/demo binding. Replay payload `demoId` validation and explicit public-field projection remain mandatory.

## Cleanup and Retention

Cleanup is a deterministic service operation, not a new durable queue:

- Failed requests delete incomplete quarantine immediately.
- Successful promotion deletes quarantine after accepted verification.
- API startup or an explicit maintenance call may remove at most 1,000 quarantine objects older than one hour per invocation.
- Tests inject `now` and list only the quarantine prefix; no real sleep or broad bucket scan is allowed. Corrupt provider metadata and accepted residuals require a separate reconciliation/lifecycle control before public beta.
- Cleanup validates logical metadata before delete and cannot delete an accepted, foreign-owner, foreign-demo, or malformed object.
- Superseded or unreferenced video candidates are deleted when their binding operation fails. Broader product retention and backup lifecycle policies remain Stage 6.

The API runs upload persistence in FastAPI's synchronous worker pool rather than on the event loop. The request parser's private temporary file is the only full-size request spool for the S3 path; the adapter hashes and uploads that seekable file directly, closes it before promotion, and performs promotion inside object storage. Deployments must provision private temporary capacity for the largest admitted request and enforce the same single-upload concurrency at the ingress when multiple API processes are used.

## Configuration

Development defaults to the local adapter. Production fails startup unless the object adapter has a non-empty private bucket and bounded prefix configuration. Credentials remain server-side and may come from the runtime credential chain; examples contain placeholders only.

Expected variables:

- `ARTIFACT_STORAGE_BACKEND=local|s3`
- `OBJECT_STORAGE_BUCKET`
- `OBJECT_STORAGE_PREFIX=cs2-artifacts-v1`
- `OBJECT_STORAGE_REGION`
- `OBJECT_STORAGE_ENDPOINT_URL` (optional S3-compatible endpoint)
- `OBJECT_STORAGE_ACCESS_KEY_ID` and `OBJECT_STORAGE_SECRET_ACCESS_KEY` (optional server-side credentials; never `NEXT_PUBLIC_*`)
- `ARTIFACT_QUARANTINE_TTL_SECONDS=3600`
- `MAX_DEMO_UPLOAD_BYTES=1073741824`
- `MAX_VIDEO_UPLOAD_BYTES=2147483648`
- `MAX_REPLAY_ARTIFACT_BYTES=134217728`
- `UPLOAD_CHUNK_BYTES=1048576`

Production must not use the local adapter. Development Compose may retain local volumes; this document does not provision a real object store.

Local Compose mounts the same private artifact volume into API and parser worker containers so accepted references survive container restart and remain materializable across processes. This shared local volume is development-only and does not weaken the production S3 requirement.

## Acceptance Evidence

Automated evidence must prove:

- Local and fake-object implementations pass the same key, write, head, range, promote, delete, owner/demo binding, generation, and materialization contract.
- A valid `.dem` follows quarantine -> SHA-256 validation -> accepted promotion -> DB bind -> one current Redis dispatch.
- Wrong extension, public archive input, zero, truncated, oversized, incompatible content, interrupted stream, checksum drift, promotion failure, and metadata failure create no consumable accepted source and no parser job/payload.
- A DB-confirmed bind failure removes the accepted orphan; an uncertain commit outcome preserves it for reconciliation. Redis failure remains an explicit Stage 4 queued-work gap without weakening accepted binding.
- Abandoned quarantine cleanup passes with an injected clock and cannot delete accepted or foreign objects.
- Retry and worker processing reject missing/tampered/cross-owner/cross-demo/wrong-kind/unaccepted metadata before reading source bytes.
- Parser materialization is bounded, version/digest checked, private, and removed on every exit path.
- Owner A succeeds; owner B and anonymous/expired identities cannot read source/replay/video metadata or bytes.
- Private video full GET, HEAD, satisfiable Range, unsatisfiable Range, conditional-generation mismatch, local symlink, and object replacement tests retain Stage 2 behavior.
- User JSON, database failure fields, diagnostics, and captured logs contain no logical/physical key, provider URL, path, credential, traceback marker, or raw upload content.
- Large artifacts remain outside PostgreSQL.
- Unauthorized multipart requests consume no body bytes; authorized request envelopes are bounded and per-process upload concurrency is one.
- Render-worker media is accepted only while its exact job is `rendering`, stored as an immutable `outputArtifact` snapshot, checked against that job and requested tick contract at callback, and deleted on failed/replaced output.
- Replay-reference replacement and render-job state commit together; manifests use the current live replay reference rather than a deleted historical generation.

## Stage Boundary

Stage 3 does not provide automatic durable Redis delivery, stale-processing recovery, or terminal job reconciliation. Steam Demo import adds a bounded manual re-dispatch path for a queued job whose dispatch marker has gone stale, and the worker uses a database `queued -> processing` compare-and-set so duplicate deliveries cannot execute the same job twice. Broader automatic recovery and reconciliation remain Stage 4. Artifact Intake still guarantees that dispatch happens only after accepted artifact binding and that rejected or partial artifacts never dispatch.

Stage 3 does not add parser CPU, memory, disk, output, or wall-clock containment. Materialization is bounded storage compatibility; hostile parser isolation is Stage 5.

Formal migrations, CI/CD, production logs/metrics/traces/alerts, backup and restore exercises, and long-term retention operations remain Stage 6. Real corpus promotion remains Stage 7.
