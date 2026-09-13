# Saved clip retention and reuse acceptance

Scope: this checkout's existing xelex review flow. Users request clips from a finding or selected tick. Completed clips remain in the player's list; replaying a saved clip does not start CS2 again. Rendering new footage still uses the separately configured local worker.

## Behavior and contract

- A render request reuses a queued/rendering job or readable completed output when accepted source snapshot, resolved POV, tick bounds/rate and render preset match. Event IDs do not change footage identity. Different ranges or presets remain distinct; failed or missing output allows regeneration.
- Creation locks and refreshes the demo before reading its replay. Six PostgreSQL sessions holding the same stale demo object created exactly one job. A second event ID reused it; a different range created its own job.
- Each completed job retains its output snapshot and compact time calibration. `GET /demos/{demo_id}/render/jobs` projects optional `video` metadata with `/demos/{demo_id}/render/jobs/{job_id}/media/video`. Media delivery checks owner, demo, terminal job and immutable generation, with private GET/HEAD/Range behavior.
- A late upload cannot rebind a completed job. A new clip, failed rendering, or a development manual upload cannot remove a completed job's retained artifact.
- The frontend keeps the selected clip's fixed URL and calibration when later jobs update the default video. It resolves first completion through the job's video metadata, tolerates jobs/status arriving in different orders, and keeps round/tick/POV state shared with the tactical replay.
- Old completed jobs lacking calibration are playable only if their currently active video supplies matching trusted metadata. Unknown historical timing is not guessed.

## Automated and live checks

- Full backend suite: 474 tests passed, including stale upload, completed artifact retention, failed/missing output retry, private media boundaries and history projection.
- Frontend: all 14 helper suites, lint, typecheck and production build passed. Added reuse, player isolation, legacy/degraded clip state, private URL and first-completion/selection regressions.
- Isolated PostgreSQL concurrency result: six requests, one job; same footage across event IDs reused; a different range remained independent.
- Existing live Nuke job `fb347c00-f7e2-415e-aef4-e1435c07a229` was reused by the exact `6363–7643`, 64 tick/s, xelex request. Live job count remained three (one completed and two earlier failures); no new recording was requested.
- The retained media route returned HEAD 200 and Range 206 for 1,024 bytes. A different local development owner received 404. Existing MP4 size remained 16,426,189 bytes.
- Real browser video playback used the job-specific URL: 20 seconds, 1280×720, first finding at tick 6683 mapped to video time 5 seconds, play/pause remained synchronized, and no page errors occurred.
- Browser-only first-completion test passed: no video → A completes → B queues/completes, with A retaining its media, paused time and selection. Switching between both saved entries also passed; the one simulated generation request was intercepted and never reached the API.
- The coaching card's matching saved-clip action played the existing video without sending any render POST. This case used browser-only round bounds to match the existing short clip.
- Actual clip boundaries remained synchronized: video handed off to tactical replay past tick 7643, round 2 advanced normally, and crossing back into the video range resumed media playback. No page errors occurred.
- Final `/dashboard` check showed all four real demos. The Nuke page showed one ready clip and the two preserved earlier failures, with no horizontal overflow. Screenshot: `output/playwright/xelex-saved-clips.png`.

Browser-only multi-clip state tests use the existing video bytes with synthetic job responses. They do not create a second real recording or alter stored jobs. The existing Nuke clip is the only real completed clip in this acceptance.

Local evidence is in ignored `.local/qa/clip-backend-full-tests.log`, `clip-live-api-result.json`, `clip-real-video-browser.log`, `clip-pinning-browser.log`, `clip-cached-coaching-browser.log`, and `clip-boundaries-browser.log`. The isolated PostgreSQL test container was removed after verification; the existing App stack and local renderer remain running. Changes are uncommitted on `main`, baseline `3016207145c37f0a8fea0f00b2da80c1165f5b73`.
