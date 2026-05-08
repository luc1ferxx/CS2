# Render Worker Runner Skeleton

This directory is a standalone development skeleton for the future Windows/Linux GPU render worker.

It proves the adapter chain only:

1. Fetch a Render Worker V1 manifest from the API.
2. Locate the demo file path/reference from the manifest.
3. Use `DEV_FAKE_VIDEO_PATH` as a placeholder mp4 output.
4. Upload that placeholder mp4 to the API through the dev worker media endpoint.
5. POST the existing result callback so replay `video` metadata becomes `source = rendered`.

It does not launch CS2, Steam, OBS, ffmpeg, OpenAI, S3, or R2. It also does not control the user's computer or record the user's screen.

## Configuration

Copy `config.example.env` or export these values:

```bash
export API_BASE_URL=http://localhost:8000
export RENDER_WORKER_TOKEN=dev-render-worker-token
export WORK_DIR=.render-worker-work
export POLL_INTERVAL_SECONDS=5
export DEV_FAKE_VIDEO_PATH=/absolute/path/to/dev-placeholder.mp4
```

`DEV_FAKE_VIDEO_PATH` is optional. If it is missing or points at a file that does not exist, the runner posts a failed callback with a clear "real renderer is not connected" message.

`WORK_DIR` is used for local manifest snapshots. It is not object storage and should not contain committed media.

## Commands

Fetch the next queued `render_clip` manifest and print the plan without callback:

```bash
python3 render-worker/runner.py dry-run
```

Process a known job without callback:

```bash
python3 render-worker/runner.py dry-run {job_id}
```

Process a known job and callback completed/failed:

```bash
python3 render-worker/runner.py process-job {job_id}
```

Poll one queued job and process it:

```bash
python3 render-worker/runner.py poll-once
```

## Fake MP4 End-To-End Check

1. Start the stack:

   ```bash
   docker compose up --build
   ```

2. Create or upload a completed demo, then click `Generate Clip` in the demo detail page or call:

   ```bash
   curl -X POST http://localhost:8000/demos/{demo_id}/render/clip \
     -H "Content-Type: application/json" \
     -d '{"tickStart":0,"tickEnd":640,"tickRate":64}'
   ```

3. Dry-run the manifest:

   ```bash
   python3 render-worker/runner.py dry-run {job_id}
   ```

4. Point the runner at a small local mp4 and process the job:

   ```bash
   export DEV_FAKE_VIDEO_PATH=/absolute/path/to/dev-placeholder.mp4
   python3 render-worker/runner.py process-job {job_id}
   ```

5. Open the demo detail page. `FirstPersonReplay` should play the callback video URL because the replay contract now has `video.source = rendered`, `video.status = ready`, and a non-empty `video.url`.

## Failure Path Check

Unset or break `DEV_FAKE_VIDEO_PATH`:

```bash
unset DEV_FAKE_VIDEO_PATH
python3 render-worker/runner.py process-job {job_id}
```

The runner posts a failed callback. If the demo already has `source = manual_upload` video metadata, the API preserves it.

## Future Real Adapter

Replace only the placeholder-media section of `runner.py` with a real controlled-infrastructure adapter:

1. Resolve or download the `.dem` using `demoFilePath` or a future object-storage key.
2. Run CS2 only on managed Windows/Linux GPU workers.
3. Render the selected POV and tick range.
4. Produce mp4/HLS output.
5. Upload media through a production storage path.
6. POST the same result callback payload.

The API and frontend should not need a new contract for that replacement.
