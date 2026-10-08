# Internal Preview Packaging V1

This runbook packages the validated mock MVP for internal reviewers. It is a repeatable preview handoff, not a production launch plan.

Use this document when you need to start the app, run the release-candidate checks, validate the sample `.dem` path, do the browser smoke, and hand off evidence without introducing generated artifacts.

## Preview Purpose

The preview should demonstrate the demo-first review flow:

1. Upload or create a demo from `/dashboard`.
2. For real samples, upload only a `.dem` through the normal parser path.
3. Review replay contract diagnostics, first-person mock shell, tactical map, timeline, round review, and deterministic coaching events.
4. Request a `render_clip` job and confirm the local no-GPU fallback stays clear and non-blocking.

The audience is internal product/engineering review. The preview is safe to use for mock MVP validation, but it is not production auth, production storage, production rendering, or a public multi-user deployment.

## Source Documents

- `README.md`: product scope, architecture, local commands, API surface, and limitations.
- `docs/release_candidate_qa_v1.md`: full RC checklist and manual browser QA.
- `docs/deployment_readiness_v1.md`: runtime configuration, health, diagnostics, and deployability notes.
- `docs/cloud_preview_deploy_v1.md`: Compose preview shape and public URL contract.
- `docs/sample_demo_fixture_v1.md`: local sample `.dem` convention and smoke modes.

This document is the handoff checklist that ties those references together.

## Preflight

Start from the repository root:

```bash
cd /Users/luc1ferx/Desktop/Projects/CS2
git status --short
```

Keep existing local work intact. Do not commit or push generated artifacts, and do not delete Docker volumes as part of preview packaging unless the reviewer explicitly asks for a clean environment.

Expected ignored local artifact locations include:

- `sample-demos/`
- `samples/`
- `.local/`
- `data/`, `uploads/`, `replays/`, `summaries/`, `videos/`
- `.render-worker-work/`
- `.dem`, video files, replay blobs, parser dumps, and generated media

## Local Run

Run the standard local stack:

```bash
docker compose up --build
```

Open:

- Frontend: `http://localhost:3000`
- Dashboard: `http://localhost:3000/dashboard`
- API health: `http://localhost:8000/health`
- Safe diagnostics: `http://localhost:8000/diagnostics`

The standard stack's frontend is already a production build. For the production-auth preview shape (its Redis requires `REDIS_PASSWORD`):

```bash
export REDIS_PASSWORD="$(openssl rand -hex 24)"
docker compose -f docker-compose.yml -f docker-compose.preview.yml up --build
```

If Docker is already running from RC validation, you can keep the existing stack. Smoke-created demos and jobs are expected local review data.

## RC Command Sequence

Run the automated non-browser RC gate:

```bash
./scripts/rc_check.sh
```

The helper runs:

- `./scripts/verify.sh`
- Docker build/up
- `/health`
- `/diagnostics`
- local `/dashboard` reachability
- mandatory cloud preview smoke with mock upload, replay/coaching load, `render_clip`, media-route check, and diagnostics summary
- optional sample smoke when `SAMPLE_DEMO_PATH` is set

Manual browser QA is still required after `rc_check.sh`.

## Strict Sample `.dem` Validation

Real `.dem` samples are local-only inputs. Keep them under ignored paths and use samples you are allowed to store and upload.

For the current Dust II validation sample, the local convention is:

```bash
REQUIRE_SAMPLE_DEMO=1 \
SAMPLE_DEMO_PATH="$PWD/sample-demos/falcons-vs-furia-m1-dust2.dem" \
./scripts/rc_check.sh
```

If using the smoke script directly:

```bash
SAMPLE_DEMO_PATH="$PWD/sample-demos/falcons-vs-furia-m1-dust2.dem" \
python3 scripts/cloud_preview_smoke.py --require-sample
```

Expected successful sample output shape:

```text
sample demo completed: <demo_id> / <name> / de_dust2 / 24 rounds / 24 coaching events / Dust II calibrated calibrated
cloud preview smoke passed
```

The exact `<demo_id>` and `<name>` vary by run. Existing parsed rows are useful for UI regression checks, but strict sample validation must upload a fresh sample and wait for parser/analyzer completion.

## Manual Browser Smoke

Open `http://localhost:3000/dashboard` and verify:

- Dashboard loads with no current console errors.
- Search/filter no-result state is compact and offers clear recovery.
- Mock upload creates a demo, and the notice or table action opens it.
- Real/sample `.dem` upload parses and opens when a local sample is available.
- Failed/corrupt demo state shows compact parser metadata such as `INVALID_DEMO`, without stack traces or local paths.
- Demo Library search, status filter, map filter, sort order, rename, archive, and show archived work.
- Demo Detail summary shows file, map, calibration/fallback, round count, coaching count, parser status, media status, and latest render status.
- Replay play/pause, seek, and speed controls work.
- Round selector and round quick jumps keep first-person shell, tactical map, timeline, parser markers, and coaching cards synchronized.
- Timeline parser/coaching markers click-to-seek.
- Coaching severity, rule, and search filters work; coaching event cards click-to-seek.
- `Generate Clip` creates a `render_clip` job.
- Without a connected GPU worker, the expected fallback is `GPU worker not connected for render_clip`.
- `RenderOperatorPanel` shows latest job status, tick range, output, and compact errors.
- Desktop and mobile widths have no incoherent horizontal overflow or clipped primary controls.

For mobile, a practical smoke viewport is around `390x844`. For desktop, use a normal laptop/desktop width such as `1440x1000`.

## Safe Handling Of Smoke-Created Data

RC and browser smoke create local demos and jobs. That is expected.

Safe handling:

- Leave local smoke-created rows in the local database when preserving review evidence is useful.
- Soft-archive demos from the Dashboard when you want the default library view to stay focused.
- Use `DEV_USER_ID` or `X-Dev-User-Id` to isolate a reviewer or smoke run when needed.
- Use `/diagnostics` to confirm queue length, worker heartbeat, recent failures, and render fallback state.
- Do not commit local samples, uploaded demos, replay blobs, media files, Docker volumes, or generated storage directories.
- Do not run destructive cleanup such as `docker compose down -v`, volume deletion, or broad file deletion unless the reviewer explicitly approves a clean reset.

If a reviewer requests a clean environment, capture the current handoff evidence first, then ask for explicit approval before removing volumes or local storage.

## Preview Boundaries

These boundaries must stay visible in the handoff:

- `DEV_USER_ID` and `X-Dev-User-Id` are dev-only owner scoping, not production authentication.
- There is no session, OAuth, JWT, password login, account UI, OpenAI call, or LLM coaching workflow.
- Coaching is deterministic rules output with compact evidence metadata.
- The browser cannot directly play a `.dem` as CS2 first-person video.
- `render_clip` is a short-clip job boundary. The current local worker may mark it failed with the no-GPU message.
- Fake/manual render-worker flows prove the manifest/upload/callback contract only. They do not run CS2, Steam, OBS, ffmpeg, screen recording, or local game-client automation.
- Manual MP4 upload/calibration is a development and QA bridge, not the primary user product path.
- Local filesystem storage and Docker volumes are preview/MVP storage, not durable object storage.
- No S3/R2 credentials, cloud SDKs, production object storage migration, production telemetry, raw parser trace storage, or generated media artifacts are part of this package.

## Handoff Evidence Checklist

Record these items for internal review:

- Git SHA or branch name.
- `git status --short` output, including any intentional uncommitted docs/test changes.
- `./scripts/rc_check.sh` result.
- Strict sample command and result, or a clear note that no sample was configured.
- Browser smoke result for `/dashboard`, sample detail, failed demo state, desktop, and mobile.
- `/health` status.
- `/diagnostics` summary, including queue length, heartbeat state, and recent render fallback.
- Any created sample/mock demo IDs that reviewers should open.
- Known limitations and any environment-specific notes.

## Recommended Handoff Summary

Use this shape in the review note:

```text
Internal preview package: PASS/FAIL
Automated RC gate: PASS/FAIL
Strict sample .dem: PASS/FAIL/SKIPPED
Browser smoke: PASS/FAIL
Sample result: <map> / <round_count> rounds / <coaching_count> coaching / <calibration>
Health: ok/degraded
Diagnostics: queue=<n>, heartbeat=<alive|stale|missing>, renderWorker=<status>
Known limitations: local storage, dev-only owner boundary, no real GPU rendering
Changed files: <docs/tests>
```

