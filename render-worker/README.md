# Render Worker Runner Skeleton

This directory is a standalone development skeleton for a future Windows/Linux GPU render worker. It proves the Render Worker V1 adapter chain without launching CS2, Steam, OBS, ffmpeg, OpenAI, S3, or R2. It never controls the user's computer or records the user's screen.

## Adapter Architecture

`runner.py` fetches render manifests and delegates render-specific behavior to adapters. Normal processing claims a queued manifest and moves it to `rendering`; dry-run inspection passes `claim=false` so it can inspect a plan without changing job state.

- `adapters/base.py`: shared result types, callback payload helpers, and adapter errors.
- `adapters/fake_video.py`: `FakeVideoAdapter`, the existing dev fake mp4 flow.
- `adapters/cs2_manual.py`: `CS2ManualAdapter`, a manual probe that prepares files and callback metadata for an operator.

The callback contract stays the same: adapters either upload or reference an mp4, then POST `/render-worker/jobs/{job_id}/result`. The API rejects callbacks for terminal jobs so late worker results cannot overwrite completed or failed state.

## Configuration

Copy `config.example.env` or export these values:

```bash
export API_BASE_URL=http://localhost:8000
export RENDER_WORKER_TOKEN=dev-render-worker-token
export WORK_DIR=.render-worker-work
export POLL_INTERVAL_SECONDS=5
```

Fake adapter:

```bash
export DEV_FAKE_VIDEO_PATH=/absolute/path/to/dev-placeholder.mp4
```

Manual adapter:

```bash
export CS2_INSTALL_DIR="/absolute/path/to/Counter-Strike Global Offensive"
export STEAM_USER_DATA_DIR=/absolute/path/to/Steam/userdata
export CS2_MANUAL_OUTPUT_FILENAME="{job_id}.mp4"
```

The manual adapter checks that `CS2_INSTALL_DIR`, `STEAM_USER_DATA_DIR`, and `WORK_DIR` are configured, but it does not start anything. Tests use temporary directories; a normal dev machine does not need real CS2 paths unless you are doing a manual probe.

## Commands

Existing fake flow:

```bash
python3 render-worker/runner.py dry-run
python3 render-worker/runner.py dry-run {job_id}
python3 render-worker/runner.py process-job {job_id}
python3 render-worker/runner.py poll-once
```

`dry-run` uses `claim=false` on the manifest endpoints. `process-job`, `poll-once`, `prepare-job`, and `complete-prepared-job` use claimed manifests because they are part of an actual processing flow.

Manual adapter flow:

```bash
python3 render-worker/runner.py prepare-job --job-id {job_id} --adapter cs2-manual
python3 render-worker/runner.py complete-prepared-job --job-id {job_id}
python3 render-worker/runner.py complete-prepared-job --job-id {job_id} --video-path /absolute/path/to/rendered.mp4
```

## FakeVideoAdapter

`FakeVideoAdapter` preserves the previous skeleton behavior:

1. Fetch a manifest by job id or through `poll-once`.
2. If `DEV_FAKE_VIDEO_PATH` points at an existing local mp4, upload it through `/render-worker/jobs/{job_id}/media`.
3. POST a completed callback with the returned `/media/videos/...` URL.
4. If `DEV_FAKE_VIDEO_PATH` is missing or invalid, POST a failed callback with a clear "real renderer is not connected" message.

This remains a dev bridge only. It does not render real CS2 footage.

## CS2ManualAdapter

`CS2ManualAdapter` is a manual probe, not automation. It does not log into Steam, start CS2, execute `playdemo`, call OBS, or run ffmpeg.

`prepare-job` creates:

```text
{WORK_DIR}/jobs/{job_id}/
  manifest.json
  instructions.md
  expected_output.json
  status.json
  output/{job_id}.mp4
```

`instructions.md` includes:

- demo file path when locally resolvable plus `demoStorageKey` / `replayStorageKey`
- map name
- POV player id / Steam ID
- `tickStart`, `tickEnd`, `tickRate`, and `roundNumber`
- recommended output file path
- manual steps: open CS2 manually, load the demo, seek to the tick range, record the clip, export an mp4 to the output path

`expected_output.json` contains the expected MP4 filename/path plus the callback metadata template that will be used after a human places the mp4 at the expected path.

`status.json` is a small operator state file with:

- `preparedAt`
- `expectedVideoPath`
- `state: waiting_for_manual_recording`
- `nextAction`

If the output mp4 exists, `complete-prepared-job` uploads it and posts a completed callback. If the output mp4 does not exist, the runner returns `waiting` and does not mark the job completed or failed.

## Manual Probe End-To-End

1. Start the local stack:

   ```bash
   docker compose up --build
   ```

2. Create or upload a completed demo, then click `Generate Clip` in the demo detail page or call:

   ```bash
   curl -X POST http://localhost:8000/demos/{demo_id}/render/clip \
     -H "Content-Type: application/json" \
     -d '{"tickStart":0,"tickEnd":640,"tickRate":64}'
   ```

3. Prepare the job:

   ```bash
   python3 render-worker/runner.py prepare-job --job-id {job_id} --adapter cs2-manual
   ```

4. Inspect `{WORK_DIR}/jobs/{job_id}/manifest.json`, `instructions.md`, `expected_output.json`, and `status.json`. On a controlled render machine, manually open CS2 and follow the instructions file.

5. Put the mp4 at the expected output path or pass a custom path:

   ```bash
   python3 render-worker/runner.py complete-prepared-job --job-id {job_id}
   # or
   python3 render-worker/runner.py complete-prepared-job --job-id {job_id} --video-path /absolute/path/to/clip.mp4
   ```

6. Open the demo detail page. `FirstPersonReplay` should play the callback URL because replay `video` metadata is now `status = ready`, `source = rendered`, and has a non-empty `url`.

The app's Render Operator panel reads API job/video status only. It does not know the local workspace path unless an operator opens the files produced by this runner.

## Future Real Adapter

Replace the manual/fake adapter with a controlled-infrastructure adapter later:

1. Resolve or download the `.dem` using `demoStorageKey`; `demoFilePath` is only a local adapter hint when the storage implementation is local filesystem.
2. Run CS2 only on managed Windows/Linux GPU workers.
3. Render the selected POV/tick range.
4. Produce mp4/HLS output.
5. Upload media through the API media endpoint or a future production storage adapter that preserves the same callback shape.
6. POST the same result callback payload.

The API and frontend should not need a new contract for that replacement.
