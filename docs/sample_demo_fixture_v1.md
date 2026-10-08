# Sample Demo Fixture V1

Sample demos are optional local inputs for proving the fresh upload/parser path. They are not part of the repository. Internal preview packaging uses the same convention; see `docs/internal_preview_packaging_v1.md` for the strict sample command and handoff evidence checklist.

## Local Convention

Use an ignored local directory and an absolute path:

```bash
mkdir -p sample-demos
# place sample.dem here
export SAMPLE_DEMO_PATH="$PWD/sample-demos/sample.dem"
export SAMPLE_DEMO_NAME="Local Sample Demo"
```

Ignored local locations include `sample-demos/`, `samples/`, `.local/`, root storage directories, `.dem`, demo archive names such as `*.dem.zip`, and common video outputs. The product smoke path should use a `.dem`; archive inputs are retained only as development compatibility. Do not commit real demos, replay blobs, media files, parser dumps, or generated storage artifacts. The one committed file derived from real demos is the aggregate manifest described under [Real-Demo Manifest](#real-demo-manifest).

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

The script validates the path, uploads it through a chunked upload session (`POST /uploads/sessions`, one `PUT` per part, then `complete`, as the browser does), waits for parser completion, and prints the created demo id, name, map, round count, coaching event count, and map calibration/fallback status.

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

With `SAMPLE_DEMO_PATH` set, `rc_check.sh` first checks the sample offline against the real-demo manifest (below) and then runs the upload smoke. A sample the manifest does not know is skipped there; a known sample whose parse no longer matches fails the gate.

See `docs/release_candidate_qa_v1.md` for the full RC checklist and manual browser smoke expectations.

## Real-Demo Manifest

`backend/tests/fixtures/real_demo_manifest.json` records what the parser, normalizer and analyzer make of the four local sample demos kept in the repository root (one each on Dust2, Mirage, Ancient and Nuke). It holds aggregates only:

- the key: file size and the first 16 hex characters of the file's sha256;
- the versions it was built with: demoparser2, `REPLAY_CONTRACT_VERSION`, `COACHING_RULES_VERSION`, `MATCH_SUMMARY_VERSION`;
- map name, tick rate, player count, round count and wins by side;
- frame counts and frame-row counts (per side, with height, dead, hurt, carrying the bomb);
- parser event counts per type, and how many of each carry a position and a player;
- kill, equipment-state, utility (per type, with throw origin and thrower side), key-input and gun-shot counts;
- the final score per team as start side and score, with how many team and player clan names resolved (never the names);
- coaching suggestion counts per rule and severity, and the sha256 of the analyzer output (events sorted, canonical JSON).

It never holds player ids, names, positions, events or any other demo content; `RealDemoManifestShapeTest` (always on, CI included) fails on any field or string outside that shape. The "with a position" and frame-row counts are what catch the parser's silent fallbacks: forcing `player_death`/`player_hurt` onto their prop-less retry on Ancient leaves every event count and the analyzer hash unchanged, but moves the kill and damage position counts from 154 and 501 to 0.

Check the local demos (about 10 s per demo):

```bash
./scripts/verify.sh   # its last step runs this whenever the repository root holds a .dem
PYTHONPATH=backend REAL_DEMO_MANIFEST_CHECK=1 python -m unittest discover -s backend/tests -p test_real_demo_manifest.py
PYTHONPATH=backend python -m app.cli.real_demo_manifest check [path/to/demo.dem ...]
```

Without a path the command line uses the repository-root `*.dem`. A demo whose size and hash prefix match no entry is skipped, never failed; a known one that no longer matches prints each differing count as `path: expected -> actual`. `REPLAY_V2_SAMPLE_CHECK=1 ./scripts/verify.sh` also runs the replay contract v2–v5 sample checks (`test_sample_demo_contract_v2.py`) in its backend tests step; they parse the demos again.

Regenerate the manifest, review the diff and commit it together with the change that moved it:

```bash
PYTHONPATH=backend python -m app.cli.real_demo_manifest update   # --prune drops entries for demos not given
```

Regenerate after a demoparser2 upgrade (or another parser dependency change), any change under `backend/app/parser/` that alters parser or normalizer output, a map-config transform change, an analyzer change, and every bump of `COACHING_RULES_VERSION`, `REPLAY_CONTRACT_VERSION` or `MATCH_SUMMARY_VERSION`. The versions are part of each entry, so a bump without regeneration fails the check by design.

## Manual Upload Command

For ad hoc seeding without the full smoke:

```bash
curl -H "X-Dev-User-Id: dev-user" -F "file=@${SAMPLE_DEMO_PATH}" http://localhost:8000/uploads/demo
```

This intentionally uses a normal upload endpoint: the single-request `POST /uploads/demo`, kept for curl next to the browser's chunked upload sessions, runs the same intake. Do not seed the database directly for parser smoke checks.

## Reliability Expectations

- Corrupt `.dem` files should fail with compact ingestion metadata and a clear user-facing message.
- Missing optional parser event families may still produce a partial replay and deterministic coaching output.
- A real sample smoke validates fresh upload storage, Redis dispatch, worker parsing, replay storage, and analyzer completion.
- Existing parsed rows are useful for UI regression checks, but they are not a substitute for a fresh sample upload/parser smoke.
