# Sample Demo Fixture V1

Sample demos are optional local inputs for proving the fresh upload/parser path. They are not part of the repository.

## Local Convention

Use an ignored local directory and an absolute path:

```bash
mkdir -p sample-demos
# place sample.dem here
export SAMPLE_DEMO_PATH="$PWD/sample-demos/sample.dem"
export SAMPLE_DEMO_NAME="Local Sample Demo"
```

Ignored local locations include `sample-demos/`, `samples/`, `.local/`, root storage directories, `.dem`, demo archive names such as `*.dem.zip`, and common video outputs. The product smoke path should use a `.dem`; archive inputs are retained only as development compatibility. Do not commit real demos, replay blobs, media files, parser dumps, or generated storage artifacts.

Real match demos can include player data or licensed match content. Use only samples you are allowed to keep locally and upload to the target API.

## Smoke Modes

Run the standard smoke without a sample:

```bash
API_BASE_URL=http://localhost:8000 FRONTEND_URL=http://localhost:3000 python3 scripts/cloud_preview_smoke.py
```

With no `SAMPLE_DEMO_PATH`, the script skips sample upload and exits successfully after health, frontend, mock upload, replay/coaching, render job, and media-route checks.

Run with a sample:

```bash
SAMPLE_DEMO_PATH="$PWD/sample-demos/sample.dem" python3 scripts/cloud_preview_smoke.py
```

The script validates the path, uploads through `POST /uploads/demo`, waits for parser completion, and prints the created demo id, name, map, round count, coaching event count, and map calibration/fallback status.

Require a sample in stricter preview validation:

```bash
SAMPLE_DEMO_PATH="$PWD/sample-demos/sample.dem" python3 scripts/cloud_preview_smoke.py --require-sample
REQUIRE_SAMPLE_DEMO=1 python3 scripts/cloud_preview_smoke.py
```

Missing, non-file, or failed sample parses are non-zero in required mode. A configured but invalid `SAMPLE_DEMO_PATH` is always a failure because it indicates a broken smoke configuration.

The release-candidate helper uses the same environment:

```bash
SAMPLE_DEMO_PATH="$PWD/sample-demos/sample.dem" ./scripts/rc_check.sh
REQUIRE_SAMPLE_DEMO=1 SAMPLE_DEMO_PATH="$PWD/sample-demos/sample.dem" ./scripts/rc_check.sh
```

See `docs/release_candidate_qa_v1.md` for the full RC checklist and manual browser smoke expectations.

## Manual Upload Command

For ad hoc seeding without the full smoke:

```bash
curl -H "X-Dev-User-Id: dev-user" -F "file=@${SAMPLE_DEMO_PATH}" http://localhost:8000/uploads/demo
```

This intentionally uses the normal upload endpoint. Do not seed the database directly for parser smoke checks.

## Reliability Expectations

- Corrupt `.dem` files should fail with compact ingestion metadata and a clear user-facing message.
- Missing optional parser event families may still produce a partial replay and deterministic coaching output.
- A real sample smoke validates fresh upload storage, Redis dispatch, worker parsing, replay storage, and analyzer completion.
- Existing parsed rows are useful for UI regression checks, but they are not a substitute for a fresh sample upload/parser smoke.
