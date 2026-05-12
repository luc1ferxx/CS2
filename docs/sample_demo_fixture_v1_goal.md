# Sample Demo Fixture / Demo Seed V1 Goal

## Objective

Based on the current `main` branch, implement Sample Demo Fixture / Demo Seed v1.

Do not add OpenAI, do not implement real CS2 rendering, do not change the render-worker architecture, and do not commit large `.dem`, `.zip`, video, replay, or media artifacts to git. The goal is to create a safe, repeatable way to run real demo upload/parser smoke checks when a local sample demo is available, while clearly skipping those checks when it is not.

## Start

1. `git pull origin main`
2. Read:
   - `AGENTS.md`
   - `README.md`
   - `docs/deployment_readiness_v1.md`
   - `docs/cloud_preview_deploy_v1.md`
   - `docs/parser_reliability_v2_goal.md`
   - `.gitignore`
3. Inspect:
   - `scripts/cloud_preview_smoke.py`
   - `scripts/verify.sh`
   - `backend/app/api/uploads.py`
   - `backend/app/services/demo_service.py`
   - `backend/app/services/storage.py`
   - `backend/app/parser/demo_parser.py`
   - `backend/tests/`
   - `docker-compose.yml`
   - env example files

## Requirements

### 1. Define a sample demo convention

Add a clear convention for optional local sample demos.

Suggested shape:

- `SAMPLE_DEMO_PATH=/absolute/path/to/sample.dem`
- optional `SAMPLE_DEMO_NAME`
- optional local ignored directory such as `samples/` or `.local/samples/`

The convention must support:

- local development
- Docker Compose smoke tests
- cloud preview smoke script
- skipping gracefully when no sample is configured

Do not require a sample demo for normal tests.

### 2. Prevent accidental large artifact commits

Update `.gitignore` if needed so large local inputs/outputs are not committed.

Ignore at least:

- `*.dem`
- `*.dem.zip` or demo archives if appropriate
- sample fixture directories
- generated replay/media artifacts
- local storage directories
- video outputs such as `*.mp4`, `*.mov`, `*.webm` where appropriate

Be careful not to ignore source files or small intentional test fixtures if any exist.

### 3. Improve smoke script sample behavior

Update `scripts/cloud_preview_smoke.py` or related smoke tooling so it:

- detects `SAMPLE_DEMO_PATH`
- validates the path exists before upload
- uploads the sample when present
- waits/polls until parse completes or fails
- prints map/round/coaching counts on success
- prints a clear skip message when absent
- exits successfully when the sample is absent, unless an explicit `--require-sample` or env flag is set
- exits non-zero when a required sample fails

Keep local mock upload smoke intact.

### 4. Add optional seed/import helper if useful

If the repo already has a practical place for scripts, add a small helper such as:

- `scripts/seed_sample_demo.py`
- or a documented `curl` command

It should help upload a configured sample demo to the running API and print the created demo ID/status.

Avoid adding a database-only seed path that bypasses normal upload/parser behavior.

### 5. Document sample acquisition and usage

Update docs:

- `README.md`
- `docs/deployment_readiness_v1.md`
- `docs/cloud_preview_deploy_v1.md`
- optionally add `docs/sample_demo_fixture_v1.md`

Document:

- where to place a local sample `.dem`
- how to set `SAMPLE_DEMO_PATH`
- how to run smoke with and without a sample
- how to require a sample in stricter preview validation
- why large demos are not committed
- licensing/privacy caution for real match demos

### 6. Keep parser reliability expectations explicit

Make sure docs explain:

- corrupt `.dem` should fail clearly
- missing optional parser event families can still produce partial success
- real sample smoke validates fresh upload/parser path
- existing parsed rows are not enough to validate fresh parser ingestion

## Preserve

Do not regress:

- `./scripts/verify.sh`
- cloud preview smoke without sample
- mock upload
- real `.dem` upload
- parser reliability v2 failure handling
- Demo Library search/filter/sort
- rename/archive
- dev owner boundary
- storage boundary compatibility
- cloud preview deploy docs/config
- round review
- rules analyzer v3
- coaching UI
- tactical map sync
- Generate Clip / `render_clip`
- RenderOperatorPanel
- fake/manual render-worker callback flows

## Tests

Add focused tests if code behavior changes.

Possible tests:

- smoke helper skips cleanly when sample env var is absent
- smoke helper errors when required sample is missing
- sample path validation rejects missing/non-file paths
- upload helper builds request using normal API endpoint

Do not add large binary fixtures.

## Verification

Run the relevant checks before committing:

```bash
./scripts/verify.sh
docker compose build
docker compose up -d
curl http://localhost:8000/health
python3 scripts/cloud_preview_smoke.py
```

If a local sample is available, also run:

```bash
SAMPLE_DEMO_PATH=/absolute/path/to/sample.dem python3 scripts/cloud_preview_smoke.py
```

If a require-sample mode is added, verify both absent and present behavior as practical.

Manual smoke:

- dashboard loads
- mock upload still works
- sample upload parses when configured
- sample parsed demo detail opens
- replay/map/timeline sync still works
- coaching click-to-seek still works
- Generate Clip still creates a render job

## Completion

Commit and push to `origin/main` with a concise imperative commit message.
