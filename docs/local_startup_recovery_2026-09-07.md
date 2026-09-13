# Local startup recovery — 2026-09-07

## Root cause and repair

Docker Desktop could not finish starting, so the App at `localhost:3000` and its API were unavailable. Its original AppData `Docker` root was a junction to `D:\DockerData`, and damaged runtime socket entries prevented the backend from binding the Windows AF_UNIX socket `sailor-ingest.sock`.

The repair also encountered Codex's MSIX AppData redirection. Paths that appeared to be under the host's C: AppData directory could resolve inside the packaged application's virtual filesystem. This caused intermediate cross-drive errors and made successful checks or changes in that virtual view insufficient evidence of a host repair. Launching through a newly created `Shell.Application` object alone did not escape the redirected context.

The successful repair ran through an existing Explorer process's `Document.Application.ShellExecute`. Before changing Docker directories, `.local/qa/repair_docker_from_desktop.ps1` created a fresh probe and used `GetFinalPathNameByHandleW` to verify that it physically resolved to the host's `C:\Users\Jxx\AppData\Local` directory. The script refuses to change Docker if that check shows redirection.

The final host layout has a normal `C:\Users\Jxx\AppData\Local\Docker` directory and a runtime directory physically at `C:\Users\Jxx\AppData\Local\Docker\run`. Only its `wsl` subdirectory is a junction to the existing `D:\DockerData\wsl`. The old secrets-engine runtime directory was preserved as `C:\Users\Jxx\AppData\Local\docker-secrets-engine-host-backup-20260907-081140` before fresh runtime directories were created. Its contents were not copied into the repository or logs.

Existing WSL disks and Docker volumes remain on D:. No Docker factory reset, volume deletion, disk replacement, or WSL unregister was used. The host repair transcript records the physical C: runtime and D: WSL paths; virtual-layer changes made earlier are not counted as host repair evidence.

The unused Codex-private `LocalCache\Local\Docker` shadow was subsequently renamed in place to `Docker-codex-repair-shadow-backup-20260907`, preserving its contents while allowing later diagnostic reads to see the real host files. This did not move the live host runtime or `D:\DockerData`. For future automated recovery, use the existing Explorer process and verify physical paths before touching AppData; launching another child PowerShell directly from Codex retains the redirected view. See Microsoft's [MSIX virtualization documentation](https://learn.microsoft.com/en-us/windows/msix/desktop/flexible-virtualization).

## Startup entry point

On this configured PC, double-click `Start CS2 Coach.cmd` at the repository root, or run:

```powershell
& C:\Users\Jxx\Desktop\CS2\scripts\start-local.ps1
```

The launcher waits for Docker, uses the existing `cs2` and `cs2-renderer` Compose projects, starts the App and CSDM database, checks readiness, and starts the Windows recording worker. It loads the ignored local loopback/external-worker configuration and preserves existing volumes. The double-click entry opens `/dashboard` after success and leaves errors visible after failure.

`-SkipRenderer` starts the App alone. `-PreflightOnly` checks local startup files without starting services. The local installation is required; this script is not a software installer for another PC. Recording remains limited to clip jobs submitted through the App, currently for xelex.

The recording launcher now checks the saved process start time and actual recording lock rather than trusting a stale PID. It reports readiness only after API/database checks and lock acquisition; detailed worker configuration or recording failures remain visible in `.local/renderer/worker.log` and `worker-error.log`.

## Library loading fix

Browser verification found another startup-facing problem: Dashboard began polling every 1.8 seconds while the first library request was still loading. Every new request invalidated the preceding request ID, so responses taking longer than the polling interval never populated the list. The fix waits for initial loading to finish and schedules later polls only after the preceding poll resolves. It preserves protection against stale results overwriting newer user actions.

## Verification status

- Passed: PowerShell syntax checks for both launch scripts.
- Passed: Windows PowerShell 5.1 preflight from the repository and from `C:\Windows`, confirming independence from the working directory.
- Passed: isolated checks for absent, Python `msvcrt`-held, and released recording locks (`.local/qa/verify-startup-lock.ps1`).
- Passed: host-context physical-path checks and Docker engine recovery; Docker reports version 29.7.2. `.local/qa/docker-host-repair-result.json` records `ready` at `2026-09-07T15:12:18Z`.
- Passed: unified startup of the five App containers and CSDM database, API/frontend readiness checks, and recording database readiness. The transcript is `.local/qa/docker-host-repair.log`.
- Passed: recording worker PID 34740 is alive with the saved start time and holds the OS recording lock. A subsequent read found only its normal `polling` / waiting-for-jobs message, no configuration or API errors, and an empty `worker-error.log`.
- Passed: repeating the unified startup exits 0 and reuses verified renderer PID 34740; no duplicate worker is created (`.local/qa/startup-repeat-check.log`).
- Passed: the real Dashboard displays all four completed matches. A browser-only regression delayed responses by 3.2 seconds and simulated one active job: initial loading completed, one non-overlapping poll observed completion, and real API responses were restored afterward. Evidence: `.local/qa/dashboard-slow-poll-browser.log`; screenshot: `output/playwright/startup-recovered-library.png`.
- Passed: the stored xelex clip decodes as 20 seconds at 1280 x 720. First finding seeks to 5 seconds; playback advances beyond 6 seconds, pauses, and remains synchronized with the shared Demo tick. No page errors occurred.
- Passed: the Dashboard helper regression, frontend lint, TypeScript checks, and production build. The native build disables Next telemetry (`NEXT_TELEMETRY_DISABLED=1`) to avoid unrelated AppData writes.
