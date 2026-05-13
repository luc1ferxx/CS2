# Release Candidate QA V1 Goal

## Objective

Based on the current `main` branch, implement Release Candidate QA v1.

Do not add OpenAI, do not implement real CS2 rendering, do not replace the dev-only owner boundary with production auth, and do not add major product features. The goal is to create a repeatable release-candidate validation process for local and preview demos so regressions are easier to catch before sharing the app.

## Start

1. `git pull origin main`
2. Read:
   - `AGENTS.md`
   - `README.md`
   - `docs/deployment_readiness_v1.md`
   - `docs/cloud_preview_deploy_v1.md`
   - `docs/sample_demo_fixture_v1.md`
   - `docs/product_polish_demo_flow_v1_goal.md`
3. Inspect:
   - `scripts/verify.sh`
   - `scripts/cloud_preview_smoke.py`
   - `docker-compose.yml`
   - `docker-compose.preview.yml`
   - backend health/diagnostics endpoints
   - dashboard and demo detail frontend flows if needed

## Requirements

### 1. Add a release-candidate QA runbook

Add a focused doc such as `docs/release_candidate_qa_v1.md`.

It should define the RC validation flow for:

- local Docker development
- cloud preview deployment
- optional sample `.dem` validation
- manual browser smoke
- known limitations and non-goals

Keep the runbook practical and executable.

### 2. Define the required RC checklist

The checklist should include:

- clean git status
- `./scripts/verify.sh`
- `docker compose build`
- `docker compose up -d`
- `curl http://localhost:8000/health`
- `curl http://localhost:8000/diagnostics`
- no-sample smoke: `python3 scripts/cloud_preview_smoke.py`
- optional sample smoke with `SAMPLE_DEMO_PATH`
- strict sample smoke with `--require-sample` when a sample is available
- dashboard loads
- mock upload creates and opens a demo
- real/sample `.dem` upload parses when configured
- corrupt `.dem` failure is clear
- Demo Library search/filter/sort/rename/archive
- Demo Detail summary is readable
- replay play/pause/seek/speed
- round review quick jumps
- tactical map sync and calibration/fallback badge
- timeline parser/coaching markers click-to-seek
- coaching filters/search/cards
- Generate Clip fallback and RenderOperatorPanel status
- `/media/...` URL smoke where available
- desktop and mobile browser pass with no current console errors

### 3. Add an optional RC helper script if useful

If it fits the existing scripts style, add a small helper such as:

- `scripts/rc_check.sh`

It should run the non-browser checks in order:

- `./scripts/verify.sh`
- Docker build/up
- `/health`
- `/diagnostics`
- `scripts/cloud_preview_smoke.py`

If `SAMPLE_DEMO_PATH` is present, it should run sample smoke too. If `REQUIRE_SAMPLE_DEMO=1` is set, absence or failure of the sample should fail the script.

Keep it macOS-friendly and simple.

Do not replace manual browser QA; document what still requires a human pass.

### 4. Align docs and smoke wording

Update:

- `README.md`
- `docs/deployment_readiness_v1.md`
- `docs/cloud_preview_deploy_v1.md`
- `docs/sample_demo_fixture_v1.md`
- `AGENTS.md` if repository workflow guidance changes

Make sure the docs point to the same RC checklist and do not give conflicting commands.

### 5. Preserve safe preview boundaries

The RC docs must explicitly state:

- dev owner boundary is not production auth
- fake/manual render is not real GPU rendering
- local filesystem storage is still a preview/MVP path
- uploaded demos are untrusted
- no OpenAI/LLM coaching is part of this scope
- do not commit `.dem`, video, replay, or generated media artifacts

### 6. Add tests only if behavior changes

If a new helper script has logic, add lightweight tests only if practical in the current test setup.

Do not add brittle browser automation or large fixtures.

## Preserve

Do not regress:

- `./scripts/verify.sh`
- cloud preview smoke
- sample demo smoke
- diagnostics endpoint
- health endpoint
- mock upload
- real `.dem` upload
- corrupt `.dem` failure handling
- Demo Library search/filter/sort
- rename/archive
- dev owner boundary
- storage boundary compatibility
- parser reliability v2
- map calibration/fallback status
- product polish demo flow
- round review
- rules analyzer v3
- coaching UI
- tactical map sync
- Generate Clip / `render_clip`
- RenderOperatorPanel
- fake/manual render-worker callback flows

## Verification

Run the relevant checks before committing:

```bash
./scripts/verify.sh
docker compose build
docker compose up -d
curl http://localhost:8000/health
curl http://localhost:8000/diagnostics
python3 scripts/cloud_preview_smoke.py
```

If an RC helper script is added, run it:

```bash
./scripts/rc_check.sh
```

If a local sample demo is available, also run:

```bash
SAMPLE_DEMO_PATH=/absolute/path/to/sample.dem python3 scripts/cloud_preview_smoke.py --require-sample
```

Manual browser smoke:

- dashboard normal, empty/no-result, and failed states if practical
- mock upload and open demo
- sample/real upload and open demo when configured
- corrupt demo failure message
- replay/map/timeline sync
- round review quick jumps
- coaching click-to-seek
- Generate Clip fallback
- RenderOperatorPanel status
- desktop/mobile views have no current console errors

## Completion

Commit and push to `origin/main` with a concise imperative commit message.

