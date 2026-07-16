# Rules-Based 2D Public Beta Launch V1

- Status: Phase 0 product and launch decision record
- Repository baseline reviewed: `235371e`
- Implementation status: documentation only; later delivery phases are not started by this record

This document fixes the product boundary, delivery order, and evidence required to move the current mock MVP toward a beta. It is based on the current repository behavior and the boundaries documented in `README.md`, `CS2_DEMO_AI_COACH_IMPLEMENTATION_PLAN.md`, `docs/deployment_readiness_v1.md`, and `docs/deployment_target_decision_v1.md`.

## Decision

V1 is a **rules-based 2D public-beta product target**.

The primary product promise is:

```text
upload .dem
  -> parse asynchronously
  -> review the match in a 2D tactical replay
  -> navigate rounds and the shared timeline
  -> inspect deterministic, evidence-linked coaching
```

The beta is successful only if that loop is reliable with real `.dem` files and safe for multiple owners. An LLM, generated coaching prose, or real first-person GPU video is not required for V1 completion and must not become a dependency of the core loop.

The current repository is a mock MVP and internal-preview implementation, not evidence that the production gaps below are already closed. Existing mock-demo, manual-MP4, first-person shell, and render-worker adapter paths may remain useful for development and QA, but they do not redefine the V1 product promise.

This decision remains authoritative for V1 unless an explicit later product decision changes it.

## Core User Journey

1. The user enters the working Demo Library and chooses the real `.dem` upload path. The synthetic mock-demo path remains an internal smoke aid, not the beta's proof of real ingestion.
2. The system accepts the upload only after applying the production intake policy. It creates an owner-scoped demo record and exposes a clear asynchronous ingestion state.
3. The user can leave or refresh while the demo moves through queued, parsing, and analyzing states. The task must survive recoverable infrastructure or worker failures without becoming permanently lost or permanently queued.
4. The parser extracts the supported match, round, player, sampled-position, combat, objective, and utility data on a best-effort basis. Optional event-family absence is a degraded partial success when the replay remains valid; it must not be misreported as full fidelity.
5. Replay normalization stores a backward-compatible replay contract. Invalid optional fields are ignored or degraded safely, unknown maps use explicit fallback metadata, and unsafe parser output cannot escape the parser boundary.
6. The deterministic rules analyzer creates compact coaching events with rule identity and evidence that can be tied back to rounds, ticks, players, and related parser events when available. No LLM call is required.
7. When processing completes, the user opens Demo Detail and reviews the 2D tactical map, round list, timeline, parser markers, and coaching cards through one shared tick and selected-round state.
8. Selecting a round, timeline point, parser marker, or coaching event moves the other review surfaces to the same point in the match. The user can filter coaching and inspect the evidence behind a rule result.
9. If ingestion fails, the library and detail views show a safe, compact failure category. A retry is offered only when the failure is retryable and the authorized source artifact still exists; otherwise the user receives a clear terminal state and can upload another `.dem`.
10. The demo, replay, coaching, diagnostics, source artifact, and any derived media remain private to the owning user throughout the flow.

## V1 In Scope

### Demo Library

- A dense working library rather than a marketing landing page.
- Real `.dem` upload, bounded status polling, ingestion progress, safe failure summaries, and owner-scoped retry when the stored source permits it.
- Search, status and map filters, sorting, inline rename, and soft archive.
- Empty, loading, fetch-failed, archived-only, and no-result states with direct recovery actions.
- Direct access to an owned archived demo by ID while archived items remain hidden from the default library view.

### Real parser and replay contract

- Asynchronous real `.dem` parsing through the supported parser path.
- Best-effort extraction of match metadata, rounds, roster, sampled player positions, kills/deaths, and supported damage, round, bomb, and utility event families.
- Backward-compatible normalization, stable player identity, safe tick and coordinate handling, and compact contract diagnostics.
- Explicit partial/degraded states when optional event families are absent or malformed.
- Explicit map calibration confidence and safe fallback behavior for unknown maps.

### Rules-based 2D review

- A 2D tactical replay driven by parsed positions and map metadata.
- Round selection, play/pause, seek, speed control, quick jumps, and timeline markers.
- One synchronized tick/round state across the tactical map, round review, timeline, parser markers, and coaching cards.
- Deterministic and explainable coaching with severity and rule filtering, search, click-to-seek, and compact evidence metadata.
- Compact replay and ingestion diagnostics that help reviewers distinguish healthy, partial, degraded, retryable, and terminal states.

### Failure and retry behavior

- Safe classification of invalid or unreadable demos, unsupported parser input, missing essential match metadata, missing frames, normalization failure, storage read failure, and unexpected parser failure.
- Optional parser event-family absence handled as partial success rather than whole-parse failure when the core replay remains usable.
- Owner-authorized retry from the stored source only when the failure policy marks the attempt retryable.
- Clear handling of dependency outages, stale work, unavailable artifacts, and terminal failure without exposing secrets, stack traces, local paths, raw parser data, or upload contents.

## Explicit Non-Goals

The following are not V1 commitments and cannot be used as V1 launch blockers or marketing claims:

- LLM chat, conversational coaching, generated coaching prose, or any OpenAI or other model dependency.
- Real CS2 first-person GPU rendering, whole-match rendering, or a production GPU worker fleet.
- User-uploaded MP4 as a primary workflow. Manual MP4 upload and calibration remain development and QA bridges only.
- Billing, subscriptions, usage-based charging, or commercial entitlement systems.
- Team workspaces, shared libraries, collaborative review, comments, or organization administration.
- Real-time match analysis, 3D replay, anti-cheat, automatic control of a user's computer or game client, screen recording, or local post-upload file access.
- A promise that all maps or all parser event families have equal fidelity. Current calibration and parser coverage must be represented honestly.

## Production Gaps

The current repository proves useful product and contract behavior, but it does not yet meet the production bar for beta traffic.

| Gap | Current repository fact | Required production outcome |
| --- | --- | --- |
| Authentication and owner authorization | Ownership is simulated by `DEV_USER_ID` and `X-Dev-User-Id`; the repository explicitly describes this as dev-only. | Every request has a trusted production identity, every owner-scoped operation authorizes that identity, and unauthenticated or cross-owner access is rejected without metadata leakage. |
| Private artifact and media access | Local preview media and artifacts are built around development routes and local storage keys, not a production private-delivery policy. | Source demos, replay artifacts, diagnostics, and derived media are private by default and accessible only through an authorized, revocable delivery path. |
| Object storage and artifact durability | Uploads, replay blobs, summaries, and videos use a local filesystem adapter and Docker volumes. | Durable private artifact storage preserves the application-level storage boundary, lifecycle policy, integrity, cleanup, and recovery requirements without placing large artifacts in PostgreSQL. |
| Safe Artifact Intake | Real `.dem` upload exists, but the reviewed preview documents do not establish production quarantine, scanning, promotion, or exhaustive abuse limits. | Untrusted input is quarantined, validated, bounded, and promoted only after passing policy; rejected and abandoned data is cleaned up safely. |
| Reliable task execution | Redis dispatch, polling workers, compact job state, and heartbeat diagnostics exist, but the heartbeat is explicitly not a production lease or scheduler. | Accepted work has durable delivery, atomic ownership, bounded recovery from stale claims, retry policy, idempotent effects, and terminal reconciliation. |
| Parser isolation | The current real parser is best-effort worker code; production CPU, memory, disk, and wall-clock isolation is not established by the reviewed docs. | Every untrusted parse runs inside enforced resource and timeout limits, fails safely, cleans temporary data, and cannot destabilize API, queue, storage, or neighboring work. |
| Database migrations | PostgreSQL metadata exists, but the reviewed docs do not establish a production migration, compatibility, or rollback-evidence workflow. | Fresh install, forward upgrade, application rollback compatibility, and data preservation are automated and rehearsed. |
| CI/CD and release control | Local verification, RC scripts, Compose preview, and manual QA runbooks exist. They are not a production deployment pipeline. | Every releasable revision passes automated gates, produces traceable artifacts, supports controlled promotion, and has a verified rollback path. |
| Observability | Health, safe diagnostics, compact snapshots, process logs, and worker heartbeat exist and are explicitly described as preview aids rather than production telemetry. | Correlated logs, metrics, traces, and actionable alerts locate failures across upload, queue, worker, parser, storage, and owner authorization without exposing sensitive data. |
| Backup and restore | Local volumes may be preserved or manually snapshotted, but no beta-grade recovery exercise is established. | Database and required artifact backups meet declared recovery objectives and are restored successfully in a repeatable exercise. |
| Real-demo confidence | Real samples are kept outside git and current smoke can skip the sample when strict mode is not enabled. | A lawful, representative real demo corpus is mandatory in beta promotion smoke; missing corpus input fails rather than silently skipping the proof. |

## Ordered Delivery Plan

The following sequence is fixed. Each numbered stage uses one dedicated branch and one pull request, and a stage cannot bundle work from a later stage. Stages 1-6 must have their pull request merged and exit evidence recorded before the next stage begins. Stage 7 uses its branch and pull request to establish the corpus and strict promotion controls; after that pull request is merged and its admission gates pass, the Invite-only Beta operational portion of Stage 7 begins. Stage 7 completes only after its operational exit evidence is recorded. Public Beta is a rollout promotion, not an eighth delivery stage.

### 1. Phase 0: Freeze V1 scope and verifiable acceptance criteria

**Objective:** Establish the rules-based 2D beta as the shared launch contract and prevent optional LLM or GPU work from displacing production fundamentals.

**Primary scope:** Product decision, core journey, in-scope and non-goal boundaries, production-gap inventory, ordered delivery plan, rollout stages, and measurable acceptance gates. This phase is documentation only.

**Exit condition:** This record contains all seven stages in order, every production gate has an objective pass/fail exercise, conflicts with older plans are explicit, and the diff contains no Stage 2-7 implementation work.

### 2. Production identity, owner authorization, and private media access

**Objective:** Replace the dev-only identity boundary and make all user data and artifacts private by default.

**Primary scope:** Trusted identity resolution, owner authorization across reads and mutations, private source/replay/media delivery, session or credential lifecycle, and production removal of local identity overrides. No identity provider or protocol is selected in Phase 0.

**Exit condition:** The complete unauthenticated and cross-owner matrix is rejected, owned workflows still succeed, private artifacts cannot be obtained by guessing or reusing unauthorized references, and the production configuration cannot trust the dev-only owner override.

### 3. Object storage and safe Artifact Intake

**Objective:** Make large untrusted inputs and derived artifacts durable, private, bounded, and safe before they can reach the parser.

**Primary scope:** Storage-adapter replacement, quarantine and promotion policy, file and content validation, size and expansion limits, integrity metadata, cleanup, retention, and private delivery. The boundary must not prevent later backup and restore work, but backup implementation and recovery exercises remain entirely in Stage 6. No cloud vendor is selected in this record.

**Exit condition:** Valid uploads complete through the private artifact lifecycle; illegal, over-limit, corrupted, abandoned, and policy-rejected uploads fail safely; no rejected artifact reaches the parser; cleanup and authorization tests pass; and large artifacts remain outside PostgreSQL.

### 4. Reliable task delivery, crash recovery, atomic claim, and idempotent execution

**Objective:** Ensure accepted parse and analysis work is neither lost nor executed with conflicting side effects.

**Primary scope:** Durable task-to-job reconciliation, atomic claim semantics, stale-work recovery, bounded retries, redelivery, idempotent artifact and database effects, worker health, and terminal-state reconciliation.

**Exit condition:** Redis and worker crash-injection scenarios recover within the declared test deadline; no accepted job is permanently missing or permanently queued; concurrent workers produce one authoritative claim; redelivery creates no duplicate terminal effects; and exhausted work reaches an explicit inspectable state.

### 5. Isolate the untrusted `.dem` parser with resource and timeout limits

**Objective:** Treat parsing as a hostile-input workload that cannot exhaust or compromise the rest of the service.

**Primary scope:** Parser execution isolation, CPU, memory, disk, output, and wall-clock limits, temporary-data cleanup, safe cancellation, bounded parser output, and failure classification.

**Exit condition:** Adversarial, corrupted, unsupported, oversized, resource-heavy, and timeout fixtures are contained by enforced limits; the parser terminates within policy; neighboring jobs and core services remain healthy; temporary artifacts are cleaned; and the user receives only a safe terminal or retryable result.

### 6. Production migrations, CI/CD, observability, and backup recovery

**Objective:** Make releases repeatable, diagnosable, reversible, and recoverable before beta users depend on the service.

**Primary scope:** Versioned database migrations, compatibility policy, automated release gates, controlled deployment and rollback, correlated logs/metrics/traces, alerting, backup schedules, restore tooling, and operational runbooks.

**Exit condition:** Fresh and upgrade migrations pass; the prior compatible application revision can be restored without data corruption; CI/CD blocks a failing revision; representative production faults are traceable through logs, metrics, traces, and alerts; and a backup is restored into an isolated environment within declared recovery objectives.

### 7. Build a real demo corpus and run an invite-only beta

**Objective:** Validate the complete rules-based 2D journey against authorized real-world data and a bounded user cohort before public exposure.

**Primary scope:** Lawful corpus acquisition and handling, representative maps and parser variants, negative fixtures, strict end-to-end corpus smoke, review-quality checks, beta operations, user feedback, and launch evidence.

**Exit condition:** Missing corpus input fails the promotion gate rather than skipping it; all required valid and negative corpus cases meet their expected results; the full upload-to-coaching journey meets declared reliability and latency objectives; invite-only operations complete without unresolved launch-blocking security, data-loss, isolation, or recovery failures; and Public Beta receives a separate go/no-go review.

## Acceptance Gates

Thresholds such as timeouts, recovery deadlines, service objectives, corpus composition, capacity targets and margins, blocking-defect taxonomy, evidence-validity windows, and beta observation periods must be versioned before the implementing stage begins. A gate is not satisfied by a successful happy-path demo, an existing database row, or an operator assertion; it needs reproducible evidence from the described test or exercise.

| Gate | Verification exercise | Required pass evidence | Blocks |
| --- | --- | --- | --- |
| Scope integrity | Review the release claims, enabled primary journey, and required dependencies against this record. | The primary journey completes without LLM, GPU video, or user MP4; no non-goal is marketed or required as V1. | Every rollout promotion |
| Unauthenticated and cross-owner denial | Create at least two owners and exercise every owner-scoped list, detail, replay, coaching, diagnostics, mutation, retry, job, source-artifact, and media capability as the owner, another owner, and an unauthenticated actor. | Owned cases pass; 100% of unauthenticated and cross-owner cases are denied; responses, logs, redirects, and artifact references leak no protected metadata or content. | Invite-only Beta and Public Beta |
| Private artifact access | Attempt direct, guessed, copied, expired, and revoked access to source demos, replay artifacts, and derived media. | Unauthorized access is denied at delivery time, access can expire or be revoked, and storage is not publicly enumerable. | Invite-only Beta and Public Beta |
| Safe upload rejection | Submit illegal type/content combinations, files above each declared limit, corrupted/truncated demos, malformed container inputs retained for compatibility testing, and abandoned uploads. | Every invalid case fails with the expected safe category; no parser work starts for intake-rejected content; no secrets, paths, stack traces, or raw content are returned; quarantined and temporary data is cleaned within policy. | Invite-only Beta and Public Beta |
| Durable crash recovery | Inject Redis unavailability and terminate workers before claim, after claim, during parse, and after intermediate writes; then restore dependencies. | Every accepted job is rediscovered within the declared recovery deadline and reaches completed or explicit terminal failure within the declared end-to-end deadline; retries are bounded; zero jobs are permanently lost or remain queued/claimed beyond the stale threshold. | Invite-only Beta and Public Beta |
| Atomic claim and idempotency | Race multiple workers for the same job and deliberately redeliver the same work before and after partial effects. | Exactly one authoritative execution owns each attempt; duplicate delivery does not create duplicate coaching rows, artifacts, or conflicting terminal transitions; reconciliation evidence matches database, queue, and storage state. | Invite-only Beta and Public Beta |
| Parser resource isolation | Run valid, corrupted, unsupported, CPU-heavy, memory-heavy, disk-heavy, oversized-output, and non-terminating fixtures concurrently with normal work. | Enforced CPU, memory, disk, output, and wall-clock limits stop policy violations; the host and neighboring work remain healthy; temporary data is removed; the affected job reaches a safe bounded result. | Invite-only Beta and Public Beta |
| Mandatory real corpus smoke | Run fresh upload, dispatch, parse, normalize, rules analysis, storage, and UI-contract checks against the declared real corpus plus negative cases in the promotion environment. Run once with corpus input missing. | The missing-corpus run fails the gate; strict mode is the beta-promotion default; every required valid sample yields its expected usable or explicitly degraded contract, and every required negative sample yields its expected safe failure. Existing rows cannot substitute for fresh ingestion. | Invite-only Beta and Public Beta |
| Migration upgrade and rollback compatibility | Start from each supported schema/application baseline, restore representative data, migrate forward, exercise the product journey, and return to the prior supported application revision under the declared compatibility policy. | Fresh install and every supported upgrade path pass; data and owner boundaries remain intact; the rollback-compatible revision operates without destructive schema assumptions or data corruption. | Invite-only Beta and Public Beta |
| CI/CD release control | Submit deliberately failing tests, migrations, security gates, and smoke checks; promote a passing revision through the declared environments; exercise rollback. | Failing revisions cannot promote, released artifacts are traceable to source and evidence, configuration contains no committed secrets, and the last compatible revision can be restored by the runbook. | Invite-only Beta and Public Beta |
| Backup restore | Restore database and required artifact backups into an isolated environment, then exercise owner access, library data, replay/coaching integrity, job reconciliation, and artifact references. | The restored system meets the declared recovery point and time objectives with no cross-owner leakage, required data loss, or broken required artifact references. | Invite-only Beta and Public Beta |
| Fault localization | Inject representative authorization, upload, storage, queue, worker, parser timeout, database, and application failures. | On-call can identify the failing request/job/artifact path and root fault within the declared diagnosis objective using correlated logs, metrics, traces, and an actionable alert; telemetry contains no secrets or uploaded content. | Invite-only Beta and Public Beta |
| Rollout quality | Run the full automated gates plus the documented manual 2D review for round/timeline/map/coaching synchronization, degraded states, and recovery actions. | Evidence is attached to the stage review, all blocking defects are resolved, and no required check is silently skipped or converted to an informational result. | Every rollout promotion |

## Rollout Stages

### Internal Preview

Purpose: validate the current product shape with maintainers and trusted reviewers while it is still explicitly a mock/dev deployment.

- The localhost or trusted-LAN Compose preview remains appropriate for the current repository boundary.
- The synthetic mock path may be used only for non-gating fast UI smoke. Any run reported as rollout or promotion evidence must use fresh real `.dem` input and cannot skip that input.
- Dev-only owner scoping, local volumes, optional real-sample smoke, and process-level diagnostics are visible limitations, not beta-ready controls.
- Remote or public exposure is not implied by this stage.

Exit from Internal Preview and admission into Invite-only Beta requires completed Stages 1-6 plus the Stage 7 corpus, strict fresh-ingestion smoke, and rollout admission controls. Every acceptance gate that blocks Invite-only Beta must pass. This starts the operational portion of Stage 7; it does not mark Stage 7 complete.

### Invite-only Beta

Purpose: operate the complete rules-based 2D journey for a bounded, identified cohort using production identity, private artifacts, reliable jobs, isolated parsing, and recoverable operations.

- Admission is controlled and cohort size is bounded by the tested capacity and support plan.
- Product claims remain limited to the 2D replay and deterministic coaching contract.
- Reliability, parse-quality, authorization-denial, resource-use, incident, and user-review evidence is collected against predeclared objectives.
- LLM and GPU capabilities remain deferred and cannot be used to mask a failure of the core journey.

Stage 7 completes only after a separate go/no-go review shows that the acceptance gates remain green for the predeclared Invite-only Beta observation period, all defects classified as launch-blocking are resolved, recovery evidence remains within its declared validity window, the real corpus meets its versioned support matrix, and the tested capacity margin plus support limits cover the proposed Public Beta enrollment.

### Public Beta

Purpose: open the already-proven rules-based 2D journey to a broader audience without expanding the product promise.

- Public Beta is a promotion decision, not an automatic consequence of elapsed time or feature count.
- All Invite-only Beta blocking gates remain release gates, including strict real-corpus ingestion, owner isolation, parser containment, recovery, and observability.
- Capacity, abuse handling, data retention, support, incident response, and rollback readiness must match the public enrollment limit.
- Any later LLM or GPU experiment is isolated from the required V1 path and receives its own product decision, branch, pull request, safety review, and acceptance criteria.

## Deferred Capabilities

### LLM coaching or generated prose

Re-evaluate only when all of the following are true:

- The deterministic coaching contract is stable, evidence-linked, and useful in the real demo corpus and Invite-only Beta.
- User research demonstrates a specific comprehension or personalization problem that deterministic templates and presentation changes cannot adequately solve.
- Privacy, data minimization, prompt-injection resistance, quality evaluation, latency, cost, fallback, and provider-outage behavior have measurable acceptance criteria.
- The model remains a non-authoritative presentation layer over structured rule evidence and is not allowed to invent match facts or block core review.
- An explicit product decision moves it into scope.

### Real first-person GPU rendering

Re-evaluate only when all of the following are true:

- The 2D replay and deterministic coaching journey has passed beta gates and demonstrated sustained user demand for event-level first-person footage.
- A controlled worker model can meet security, game/runtime licensing, resource isolation, scheduling, artifact privacy, integrity, cost, failure recovery, and operational-support requirements.
- Video remains an asynchronous enhancement; missing or failed video never blocks the 2D review flow.
- User-uploaded MP4 remains outside the primary product journey.
- An explicit product decision moves it into scope.

## Known Decision Conflicts

The older industrial implementation plan and the current Preview documents were written for different horizons. They are not edited by Phase 0. For V1 work, conflicts are resolved as follows:

| Existing document position | Phase 0 resolution |
| --- | --- |
| `CS2_DEMO_AI_COACH_IMPLEMENTATION_PLAN.md` includes OpenAI-generated coaching prose, an `ai_generating` state, AI workers, and LLM cost controls in its MVP. | V1 ends with deterministic, evidence-linked coaching. LLM chat and generated prose are deferred until the re-evaluation conditions above are met. |
| The industrial plan includes registration/login and specific password/token mechanics as early implementation work, while current Preview docs explicitly exclude production auth. | Production identity and owner authorization are mandatory Stage 2 launch work, but Phase 0 selects neither an identity provider nor a protocol. The current dev owner header is not accepted as beta authentication. |
| The industrial plan prescribes direct object-storage upload, named storage products, presigned access, CDN/WAF, and provider-shaped deployments; current Preview uses local storage and deliberately avoids cloud credentials or migration. | Durable private storage and safe Artifact Intake are mandatory Stage 3 outcomes. Vendor, topology, and concrete interface choices are deferred to that stage and must preserve the repository's storage-service boundary. |
| The industrial plan treats `.dem` and `.zip` as product uploads. Current repository direction makes `.dem` the primary product path and keeps archive handling only for development compatibility. | V1 promises `.dem` upload only. Archive compatibility must not appear as the primary beta workflow or broaden the accepted-input claim without a later decision. |
| The industrial plan targets at least 200,000 registered players and describes broad production infrastructure, while `docs/deployment_target_decision_v1.md` chooses localhost/trusted LAN as the smallest safe next internal preview. | The Preview target is valid only for Internal Preview. It is not production readiness evidence. Invite-only and Public Beta require the ordered production gates; Phase 0 makes no provider or scale-topology commitment. |
| The industrial plan includes economy/equipment snapshots, utility trajectories, broad tactical context, and other analysis depth beyond the currently documented parser contract. | V1 claims only the data and deterministic rules that the real parser, normalized replay contract, diagnostics, and corpus gates can prove. Missing optional event families and approximate map calibration must remain explicit. |
| Current Preview smoke allows the real sample path to be skipped unless strict mode is enabled. | Optional sample smoke is acceptable for fast Internal Preview checks only. Invite-only and Public Beta promotion must use strict real-corpus smoke by default, and missing corpus input must fail. |
| Current render-worker and manual-MP4 paths demonstrate a future media contract and development workflow. | They do not place GPU video or user MP4 in V1 scope. Render fallback remains acceptable; the required user value is the rules-based 2D review loop. |
| The industrial plan's original build order moves from stack setup through auth, migrations, upload/queue, mock UI, real parser/rules, and finally OpenAI. | This record's seven-stage public-beta hardening order governs V1. The older sequence remains historical architecture context and cannot justify early LLM/GPU work, moving migrations ahead of task/parser safety, or bundling work across these stages. |

If a later proposal conflicts with this record, it must first change the product decision explicitly. It must not silently alter the seven-stage order or combine a deferred capability with a production-readiness stage.
