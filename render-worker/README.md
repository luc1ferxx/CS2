# Render Worker Runner

This directory is a standalone render worker with a fake adapter (default), a manual operator bridge, and an explicitly enabled CSDM adapter for a chosen Windows render PC. The CSDM adapter launches CS2 through CS Demo Manager/HLAE and encodes a short player POV with FFmpeg. It runs outside the API containers. The product still asks users for a `.dem` upload only; local setup belongs to the operator who controls the rendering infrastructure.

## Adapter Architecture

`runner.py` fetches render manifests and delegates render-specific behavior to adapters. Normal processing claims a queued manifest and moves it to `rendering`; dry-run inspection passes `claim=false` so it can inspect a plan without changing job state.

- `adapters/base.py`: shared result types, callback payload helpers, and adapter errors.
- `adapters/fake_video.py`: `FakeVideoAdapter`, the existing dev fake mp4 flow.
- `adapters/cs2_manual.py`: `CS2ManualAdapter`, a manual probe that prepares files and callback metadata for an operator.
- `adapters/csdm.py`: `CSDMAdapter`, verified source download, CSDM analysis/recording, MP4 verification, upload, and callback.

Adapters upload an MP4 through the job media endpoint and include both returned `videoUrl` and `storageKey` in the result callback. The API validates the accepted artifact binding and rejects callbacks for terminal jobs. A claim of an already rendering job is rejected; inspect it with `claim=false` instead.

## Configuration

Copy `config.example.env` or export these values:

```bash
export API_BASE_URL=http://localhost:8000
export RENDER_WORKER_TOKEN=dev-render-worker-token
export WORK_DIR=.render-worker-work
export POLL_INTERVAL_SECONDS=5
export RENDER_ADAPTER=fake-video
```

The runner reads environment variables; it does not load `.env` automatically.

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
3. POST a completed callback with the returned private video URL and accepted artifact storage key.
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

## CSDM adapter on an operator render PC

Prerequisites are an interactive Windows desktop, compatible CS2/Steam, CS Demo Manager, its HLAE and CS2 plugin dependencies, FFmpeg/ffprobe, and CSDM's own PostgreSQL database. Put PostgreSQL's `psql.exe` directory on the worker process PATH: CSDM analysis imports temporary CSVs through `psql`. Configure CSDM's database and game/FFmpeg locations before processing a job. Steam login, if needed, is performed by the operator; this runner does not read credentials. Use a compatible release of CSDM and HLAE for the installed CS2 build and demo.

The Windows CSDM installer provides a `csdm.cmd` wrapper. To avoid shell interpretation, the worker runs its Electron executable with the bundled `resources/app.asar/cli.js` argument and sets `ELECTRON_RUN_AS_NODE=1` only for those subprocesses. Native executable CLI distributions can omit `CSDM_CLI_ENTRYPOINT`.

PowerShell example (replace paths with installed locations):

```powershell
$env:RENDER_ADAPTER = 'csdm'
$env:CSDM_EXECUTABLE = 'C:/cs2-render/tools/csdm/cs-demo-manager.exe'
$env:CSDM_CLI_ENTRYPOINT = 'C:/cs2-render/tools/csdm/resources/app.asar/cli.js'
$env:FFMPEG_EXECUTABLE = 'C:/cs2-render/tools/ffmpeg/bin/ffmpeg.exe'
$env:FFPROBE_EXECUTABLE = 'C:/cs2-render/tools/ffmpeg/bin/ffprobe.exe'
$env:WORK_DIR = 'C:/cs2-render/work'
$env:CSDM_TIMEOUT_SECONDS = '1800'
$env:CSDM_RECORDING_SYSTEM = 'HLAE'
python render-worker/runner.py dry-run JOB_ID
python render-worker/runner.py process-job JOB_ID
# Or claim and process one queued clip:
python render-worker/runner.py poll-once
# Keep a foreground worker available for the App's Generate Clip action:
python render-worker/runner.py poll
```

`poll` checks for work every `POLL_INTERVAL_SECONDS` (default 5, allowed 1–60), holds the `WORK_DIR` lock for its lifetime, and completes clips one at a time. Empty polls produce no repeated output. Existing CS2 or native scratch files prevent new claims; the worker prints a waiting message once and resumes when the condition clears. Service failures use a quiet retry with a maximum 60-second delay. A job the API answers `410` for, or its own JSON `404` `{"detail": "Render clip job not found"}` (its match was deleted while it waited or rendered), is terminal instead: the runner posts no callback, does not back off, removes that job's `WORK_DIR/jobs/{jobId}` workspace (source `.dem`, MP4, logs) and `WORK_DIR/manifests/{jobId}.json`, reports `"action": "gone"`, and moves on. `dry-run` only reports it. Any other `404` (an HTML page, a different body, as from a wrong `API_BASE_URL` such as the frontend origin) is an ordinary request failure and never removes a workspace or an operator's recording. The workspace is not the only copy on a CSDM render PC: `csdm analyze` imports every rendered match (all players' SteamID64s, names, kills and positions) into CSDM's own PostgreSQL database, and nothing here removes it; delete analysed matches there by hand (CS Demo Manager's match list) when a match is deleted or the render PC is retired.

To stop, press **Ctrl+C once** in the worker terminal. An idle worker stops immediately; an active worker finishes the current clip's verification/upload/callback and then exits without claiming another. Leave the terminal open until it prints `stopped`. Closing the terminal or killing its process can interrupt CSDM before cleanup and leave a job in `rendering`; inspect that job and any remaining recording before starting another worker. Keep the foreground worker open while using Generate Clip in the app; this command does not install a service or schedule a background task.

For an operator-managed hidden process, set `RENDER_STOP_FILE` to an absolute path, or use `--stop-file C:/cs2-render/work/renderer.stop` before `poll`. Creating that file requests the same orderly stop. The worker checks it before each claim, after each polling wait, and after the current job finishes. It never deletes or changes the flag. To start again, the operator must remove their own stop flag explicitly; a pre-existing flag causes the worker to exit without claiming a job.

Configure the **API stack** with `RENDER_WORKER_MODE=external` before creating real render jobs. This leaves clips queued for the separate worker while parsing continues normally. Default fallback mode still reports an unavailable GPU renderer. Set API URL and worker token consistently in both processes.

If the installed HLAE build cannot run the current CS2 build, set `CSDM_RECORDING_SYSTEM=CS` (or `--csdm-recording-system CS` before the runner command). This uses CSDM's native CS2 `startmovie`/`endmovie` recording and then FFmpeg. CSDM fixes `host_framerate` at 30 and reads the generated TGA sequence at 30 fps. The adapter explicitly retains yuv420p in this encoder path. Native capture temporarily uses roughly 2 GiB per 20 seconds at 720p30 before CSDM removes the raw images, so reserve scratch space on the game and worker drives. The same selected POV, tick bounds, exact output filename, and strict MP4 checks apply. This is an explicit operator choice; a failed HLAE job does not silently retry with another recorder.

Native mode also requires `CS2_INSTALL_DIR` pointing to the game installation. CSDM deletes the native scratch directories `game/csgo/movie` and `game/csgo/csdm/movie` at recording start/end. The worker refuses to claim a native job if either directory contains existing entries, and checks again immediately before recording. Preserve any existing captures elsewhere first. The worker itself never clears these directories or moves existing files.

The real adapter performs these checks and steps:

1. Validate executable configuration and acquire an OS lock in `WORK_DIR` before claiming a job. Only one runner can use that work directory. Refuse to start if CS2 is already running, because CSDM may otherwise close an existing game.
2. Fetch `/render-worker/jobs/{jobId}/source` with the worker token. Redirects are rejected. Stream to a new job workspace, enforce `MAX_SOURCE_BYTES` (default 2 GiB), and verify the exact accepted size and SHA256 from the manifest and response headers. The API resolves private storage; the worker never uses a container path or bucket credentials.
3. Run `csdm analyze source.dem`, then `csdm video source.dem START END` with the manifest's server-resolved SteamID64. CSDM records at 1280×720, 30 fps, the configured `HLAE` or native `CS` system, H.264/AAC MP4, full HUD, no X-ray, and closes the game after recording. Ranges must start at tick 96 or later and last no more than 60 seconds.
4. Accept exactly one final `sequence-1-tick-START-to-END.mp4` in the fresh output directory. Ignore intermediate `video.mp4` captures. `ffprobe` must confirm the H.264 video stream, 720p, yuv420p, 30 fps, zero start timestamp, decoded frame count, and video duration matching the requested ticks within two frames/ticks. Container duration is measured rather than synthesized.
5. Upload the verified file and submit its accepted storage key, private URL, exact requested tick bounds, and actual media duration. Store compact status and verification JSON plus local command logs under `WORK_DIR/jobs/JOB_ID`.

CSDM schedules the capture start/end at the requested ticks and pauses just before the start to exclude loading. This is the basis for `timeOriginSeconds=0`; duration checks catch loading or incomplete clips but cannot prove the displayed player or every frame's tick visually. Verify the first real clip against the selected player's HUD and a known demo event before signing off a renderer or changing its software version. [CSDM CLI](https://cs-demo-manager.com/docs/cli), [video guide](https://cs-demo-manager.com/docs/guides/video).

Failures never publish an unverified MP4. CSDM analysis can report errors while exiting with code zero, so analysis exit alone does not count as success; the matching final video and media checks are mandatory. A timed-out command is interrupted and its owned CLI process is stopped if needed. CSDM may run child processes independently; inspect its queue and close a remaining recording before retrying. The runner does not terminate Steam/CS2 processes by name. CSDM temporarily installs its CS2 plugin and normally restores game configuration afterward; an interrupted run may need operator cleanup through CSDM. Start a new render job after a terminal failure, preserving the failed workspace for diagnosis.

This is a single operator worker prototype, not an unattended rendering fleet: no service installation, account management, remote desktop control, distributed leases, HLS pipeline, or automatic software updates are included. Keep one `WORK_DIR` per render PC and run one clip at a time.

## Verification

```powershell
python -m compileall render-worker
python -m unittest discover render-worker/tests
```

Tests cover the authenticated streaming download and integrity failures, job path/POV validation, executable argument boundaries, existing-game preflight, concurrent runner exclusion, subprocess timeout, stale output rejection, frame/duration validation, accepted media callbacks, and the unchanged fake/manual adapters. Unit tests do not launch Steam, CS2, HLAE, or FFmpeg. An actual clip remains required for end-to-end acceptance.

Queue tests additionally cover quiet idle polls, serial jobs, stop during upload with the current callback completed, game-busy refusal, safe retry/backoff, lock retention while idle, and signal-handler restoration.
