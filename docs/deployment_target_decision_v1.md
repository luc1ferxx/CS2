# Deployment Target Decision V1

This decision record chooses the smallest safe deployment target for the next internal preview of the CS2 Demo AI Coach mock MVP.

It does not deploy anything, start a tunnel, expose the MacBook publicly, add credentials, or change application code.

## Decision

Recommended next target: **MacBook localhost / trusted LAN preview using Docker Compose preview shape**.

Use the already validated local production-preview shape for the next internal review when reviewers can be in the same room, on the same trusted LAN, or watching a screen share. It is the lowest-effort path, preserves the current mock MVP boundaries, keeps data local, and avoids pretending that dev-only owner scoping is production authentication.

First escalation target: **single VPS / cloud VM with Docker Compose**.

Choose a small VM only when reviewers need asynchronous remote browser access. It matches the current stack shape better than managed app platforms because the app currently needs frontend, API, worker, PostgreSQL, Redis, and mounted local volumes together.

Do not use a secure tunnel or external deployment unless explicitly approved for that preview.

## Ranked Options

| Rank | Option | Decision | Why |
| --- | --- | --- | --- |
| 1 | MacBook localhost / LAN preview | Recommended now | Smallest setup, no public exposure by default, keeps sample demos and smoke artifacts local, supports the full Compose stack. |
| 2 | Single VPS / cloud VM with Docker Compose | Best hosted path after approval | Preserves the Compose architecture and local volume assumptions while giving reviewers a stable URL. Requires host hardening and access protection. |
| 3 | MacBook + secure tunnel | Use only with explicit approval | Fast for short remote reviews, but it exposes a dev-only app through public ingress and needs separate access controls. |
| 4 | Managed app platforms | Defer | Many platforms can run web services, but this repo is currently a multi-service Compose app with worker, PostgreSQL, Redis, and local volumes. Use only if the selected platform supports that shape without a storage/auth rewrite. |

Cost notes are intentionally high level. VM and managed-platform pricing changes by provider and region, so verify official provider pricing before purchase. The MacBook paths have no new infrastructure cost beyond local machine and network usage.

## Criteria Matrix

| Criteria | MacBook localhost / LAN | MacBook + secure tunnel | Single VPS / cloud VM | Managed app platform |
| --- | --- | --- | --- | --- |
| Setup effort | Low | Low to medium | Medium | Medium to high |
| Operational burden | Low during live review | Medium because tunnel lifecycle and access must be controlled | Medium: VM patching, firewall, backups, logs | Provider-dependent |
| Internal preview security | Strongest when localhost/same trusted LAN only | Weak unless tunnel has strong access controls | Good if behind HTTPS plus auth/VPN/IP allowlist | Provider-dependent |
| Full stack support | Native Compose support | Native Compose support on MacBook | Native Compose support | Only if platform supports multi-service app, worker, database, Redis, and persistent volumes |
| CORS/media URL behavior | Simple if using LAN IP consistently | Requires tunnel URLs baked into frontend build | Requires public frontend/API origins | Provider-dependent |
| Persistence and cleanup | Docker volumes on MacBook | Docker volumes on MacBook | VM volumes/disks need backup/cleanup plan | Provider storage model |
| MVP boundary fit | Best | Acceptable only short-lived | Good for hosted preview | Risk of forcing premature production architecture |

## MacBook Localhost / LAN Preview Steps

Use localhost for a review driven from the same machine:

```bash
export FRONTEND_URL=http://localhost:3000
export NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
export BACKEND_PUBLIC_URL=http://localhost:8000
export MEDIA_URL_BASE=http://localhost:8000
export CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000

docker compose -f docker-compose.yml -f docker-compose.preview.yml up --build -d
```

For a trusted LAN review, identify the MacBook LAN IP from macOS network settings or a command such as:

```bash
ipconfig getifaddr en0
```

Then rebuild the preview frontend with LAN origins:

```bash
export PREVIEW_HOST=<mac-lan-ip>
export FRONTEND_URL=http://$PREVIEW_HOST:3000
export NEXT_PUBLIC_API_BASE_URL=http://$PREVIEW_HOST:8000
export BACKEND_PUBLIC_URL=http://$PREVIEW_HOST:8000
export MEDIA_URL_BASE=http://$PREVIEW_HOST:8000
export CORS_ORIGINS=http://$PREVIEW_HOST:3000,http://localhost:3000,http://127.0.0.1:3000

docker compose -f docker-compose.yml -f docker-compose.preview.yml up --build -d
```

Open:

- Frontend: `http://<mac-lan-ip>:3000/dashboard`
- API health: `http://<mac-lan-ip>:8000/health`
- Safe diagnostics: `http://<mac-lan-ip>:8000/diagnostics`

Operational notes:

- Keep the MacBook awake and plugged in for the session.
- Use only a trusted LAN. Do not port-forward the router.
- Allow inbound connections to ports `3000` and `8000` only for the review window if macOS firewall prompts.
- Rebuild after changing `NEXT_PUBLIC_API_BASE_URL`; Next.js bakes it into the production frontend bundle.
- Keep sample `.dem` files in ignored local paths such as `sample-demos/`.

## MacBook Secure Tunnel Considerations

Do not start a tunnel unless explicitly approved.

A tunnel can be useful for a short remote review, but it is not the default recommendation because the app has no production auth. If approved, require:

- HTTPS tunnel URLs for both frontend and API, or a reverse proxy shape that exposes both safely.
- Access protection before the app, such as provider access policy, VPN, SSO gate, or strict allowlist.
- Short TTL and explicit shutdown time.
- No broad public sharing of URLs.
- Rebuild the frontend with tunnel API URL before testing.

Two-origin tunnel shape:

```bash
export FRONTEND_URL=https://cs2-preview.example-tunnel
export NEXT_PUBLIC_API_BASE_URL=https://cs2-api-preview.example-tunnel
export BACKEND_PUBLIC_URL=https://cs2-api-preview.example-tunnel
export MEDIA_URL_BASE=https://cs2-api-preview.example-tunnel
export CORS_ORIGINS=https://cs2-preview.example-tunnel
```

This shape keeps media URLs and browser API fetches pointed at the public API tunnel. It also keeps CORS tight to the frontend tunnel origin.

Tunnel risks:

- A leaked URL exposes a dev-only app unless access is enforced outside the app.
- Uploads and replay artifacts remain on the MacBook local Docker volumes.
- Sleep, network changes, or tunnel process failure can break the preview.
- Debugging CORS/media URLs is more fragile than LAN or VM.

## Single VPS / Cloud VM Docker Compose Plan

Use this when remote reviewers need a stable URL and the team approves external hosting.

Target shape:

- One Linux VM with Docker and Docker Compose.
- Public HTTPS frontend URL.
- Public HTTPS API/media URL.
- Private PostgreSQL and Redis containers.
- API, worker, and frontend containers from this repo.
- Docker named volumes or mounted disks for PostgreSQL, uploads, replay blobs, summaries, and videos.

Domain/URL shape:

```text
https://cs2-preview.example.com        -> frontend
https://cs2-api-preview.example.com    -> API and /media/videos
```

Environment:

```bash
export FRONTEND_URL=https://cs2-preview.example.com
export NEXT_PUBLIC_API_BASE_URL=https://cs2-api-preview.example.com
export BACKEND_PUBLIC_URL=https://cs2-api-preview.example.com
export MEDIA_URL_BASE=https://cs2-api-preview.example.com
export CORS_ORIGINS=https://cs2-preview.example.com
```

High-level deployment steps:

1. Provision the VM and restrict SSH to approved operators.
2. Install Docker and Docker Compose.
3. Clone the repo at the approved commit.
4. Configure DNS for frontend and API names.
5. Put an HTTPS reverse proxy in front of ports `3000` and `8000`, or bind the Compose services behind a platform firewall/load balancer.
6. Add access protection in front of the frontend and API.
7. Set the public URL environment variables before building.
8. Start the preview:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.preview.yml up --build -d
   ```

9. Run the same smoke sequence from `docs/internal_preview_packaging_v1.md`.

Access protection recommendation:

- Because `DEV_USER_ID` is not production auth, protect the preview before traffic reaches the app.
- Prefer VPN, identity-aware proxy, SSO gate, or strict IP allowlist.
- Keep any access gate outside the app; do not add password handling or account UI to this mock MVP.
- Do not rely on obscure URLs as protection.

Persistence and cleanup:

- Keep named volumes during the review so smoke-created demos and jobs remain inspectable.
- Capture handoff evidence before stopping services.
- Stop services without deleting data when the preview is paused:

  ```bash
  docker compose -f docker-compose.yml -f docker-compose.preview.yml down
  ```

- Do not remove volumes unless explicitly approved after evidence capture.

## Managed App Platform Fit

Managed app platforms are not the smallest safe path for the next preview unless the selected platform supports this complete shape:

- Built Next.js frontend with `NEXT_PUBLIC_API_BASE_URL` set before build.
- FastAPI API with public HTTPS URL.
- Persistent backend worker process.
- PostgreSQL.
- Redis.
- Persistent upload/replay/video volumes or an approved storage adapter.
- CORS control and media URL control.
- Access protection in front of frontend and API.

If a platform cannot run a persistent worker, Redis queue, Postgres, and local artifact storage together, it will force architecture changes that are out of scope for the mock MVP preview.

## Smoke Validation

Run this after any target is started.

```bash
curl "$NEXT_PUBLIC_API_BASE_URL/health"
curl "$NEXT_PUBLIC_API_BASE_URL/diagnostics"

API_BASE_URL="$NEXT_PUBLIC_API_BASE_URL" \
FRONTEND_URL="$FRONTEND_URL" \
python3 scripts/cloud_preview_smoke.py
```

If a sample is available and the preview must prove fresh real-demo ingestion:

```bash
SAMPLE_DEMO_PATH=/absolute/path/to/sample.dem \
API_BASE_URL="$NEXT_PUBLIC_API_BASE_URL" \
FRONTEND_URL="$FRONTEND_URL" \
python3 scripts/cloud_preview_smoke.py --require-sample
```

Manual browser smoke:

- Open `$FRONTEND_URL/dashboard`.
- Create a mock demo and open it.
- Upload a sample `.dem` when available.
- Verify detail summary, Replay Contract diagnostics, replay controls, round jumps, tactical map sync, timeline/coaching click-to-seek, coaching filters, `Generate Clip`, and `RenderOperatorPanel`.
- Confirm expected local no-GPU fallback: `GPU worker not connected for render_clip`.
- Check desktop and mobile widths.
- Confirm no current browser console errors.

## Explicit Non-Goals

- No OpenAI or LLM coaching.
- No production auth, OAuth, JWT, passwords, or account management UI.
- No real CS2, Steam, OBS, ffmpeg, screen recording, or local game-client automation.
- No real GPU rendering.
- No S3/R2 credentials, cloud SDKs, or storage migration in this decision.
- No public tunnel, external deploy, DNS change, or firewall change without explicit approval.
- No committing `.dem`, video, replay blobs, parser dumps, local DB/storage, Docker volumes, or generated media artifacts.
- No destructive cleanup commands without explicit approval.

## Risks

- The app has dev-only owner scoping, so any remote target must be protected outside the app.
- Local volumes are acceptable for the mock preview but are not durable production storage.
- LAN previews depend on the MacBook staying awake and on stable local network conditions.
- Tunnel previews are easy to expose too broadly and are fragile if URLs change.
- VM previews add host patching, HTTPS, firewall, backup, and cleanup responsibilities.
- Managed platforms may require architecture changes if they do not support the current worker/Redis/Postgres/volume shape.

## Rollback And Cleanup Notes

MacBook LAN:

- Stop sharing the LAN URL and stop the Compose stack when done.
- Preserve volumes until review evidence is captured.
- Do not delete volumes or local sample directories unless explicitly approved.

Tunnel:

- Shut down the tunnel immediately after the approved review window.
- Revoke access links or access policies that were created for the review.
- Keep local volumes until evidence is captured.

VPS/VM:

- Roll back by checking out the prior commit and rebuilding the same Compose stack.
- Stop services with `docker compose ... down` to pause the preview while preserving data.
- Snapshot or back up volumes before any cleanup.
- Destroy VM disks/volumes only after explicit approval and after confirming no needed evidence remains.

## Final Recommendation

For the next internal preview, run **MacBook localhost / trusted LAN preview** with the production-built Compose override. It is the smallest safe path and best matches the mock MVP boundaries.

If reviewers need remote asynchronous access, request approval for a **single VPS / cloud VM Docker Compose preview** with HTTPS and access protection in front of both frontend and API.

Do not use a public tunnel as the default. Use it only for a short, approved review window with strong access control and explicit shutdown.
