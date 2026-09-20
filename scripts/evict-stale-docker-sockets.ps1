[CmdletBinding()]
param(
    [switch]$Quiet,
    # Skip the engine-up guard. The launcher passes this only after its own
    # Test-DockerEngine has already confirmed the engine is down.
    [switch]$Force,
    # Diagnose without deleting: list what is there and whether the anti-cheat
    # drivers are running. The launcher uses this after Docker Desktop has been
    # started, when any socket present may belong to the backend coming up.
    [switch]$ReportOnly
)

# Preflight/recovery for Docker Desktop's stale AF_UNIX socket startup crash on
# this machine. Run this only while the Docker engine is DOWN; it refuses to
# touch anything while a live engine is reachable (a running dockerd owns those
# sockets and moving them would break it).
#
# ROOT CAUSE (diagnosed 2026-09-18)
# ---------------------------------
# A third-party kernel filesystem minifilter -- Riot Vanguard (driver `vgk`)
# and/or FACEIT anti-cheat (`FACEIT`, `FACEIT_IOMMU`), all boot-resident on this
# host -- refuses to open, rename, or delete any file carrying the AF_UNIX
# socket reparse tag when it lives under %LOCALAPPDATA% or %APPDATA%. The
# operation fails with Win32 error 1920 ("The file cannot be accessed by the
# system."). Plain files in the same folders are unaffected; only the AF_UNIX
# reparse tag is blocked, and the %LOCALAPPDATA%\Temp subtree is exempt.
#
# Docker Desktop's backend (com.docker.backend.exe) tries, on startup, to clear
# a leftover socket by renaming it (engine.sock -> engine.sock.stale) before
# re-binding. When a *reparse* socket survives a previous ungraceful shutdown,
# that rename hits 1920 and the Secrets Engine -- and the whole backend --
# crashes before the engine comes up:
#
#   initializing Secrets Engine: listening on unix://.../docker-secrets-engine/
#   engine.sock: rename ...engine.sock ...engine.sock.stale: The file cannot be
#   accessed by the system.
#
# The failure is INTERMITTENT: it recurs only when a stale *reparse* socket is
# present at start. A clean shutdown that leaves the socket dir empty -- or
# leaves only a plain (non-reparse) socket -- starts fine.
#
# WHAT THIS SCRIPT CAN AND CANNOT DO (from an ordinary, non-elevated token)
# ------------------------------------------------------------------------
#   * It CAN delete stale sockets that are in a plain / non-reparse state, plus
#     any leftovers outside the filtered subtree. That safely clears the common
#     leftover case so the next start is clean.
#   * It CANNOT delete or rename a socket that is still a reparse point inside
#     %LOCALAPPDATA%. Every in-place method was verified refused with 1920
#     (Remove-Item, [IO.File]::Delete, cmd del, [IO.File]::Move, `fsutil
#     reparsepoint delete`, and backup-semantics DELETE_ON_CLOSE), and renaming
#     or moving the parent directory is refused with Access Denied even for a
#     plain directory -- so the old "move the whole socket dir aside" trick does
#     NOT work here and has been removed. Unloading or excluding the minifilter
#     needs administrator rights this token lacks; redirecting the EFS-encrypted
#     docker-secrets-engine dir elsewhere would silently drop its encryption.
#     Neither is done here.
#
# For the reparse-stuck case, the only recovery that works WITHOUT weakening the
# machine's security posture is operational and is printed by this script:
# fully quit Docker Desktop (or reboot) so the stale socket is cleared during
# re-initialisation, then start again. NEVER use "Reset to factory defaults" /
# delete Docker data to work around this -- that destroys volumes and is not
# what the error calls for.
#
# Exit codes: 0 = no stale reparse sockets remain (clean, or everything stale
# was cleared). 3 = at least one reparse socket is stuck and manual recovery is
# needed. The launcher branches on this.

$ErrorActionPreference = 'Stop'

function Write-Info([string]$msg) { if (-not $Quiet) { Write-Host $msg } }

$localAppData = $env:LOCALAPPDATA
if (-not $localAppData) { $localAppData = Join-Path $env:USERPROFILE 'AppData\Local' }

# The socket directories Docker Desktop binds under and clears on startup.
$socketDirs = @(
    (Join-Path $localAppData 'Docker\run'),
    (Join-Path $localAppData 'docker-secrets-engine')
)

function Test-EngineReachable {
    $docker = (Get-Command docker.exe -ErrorAction SilentlyContinue)
    if (-not $docker) { return $false }
    $probe = New-Object System.Diagnostics.Process
    $probe.StartInfo.FileName = $docker.Source
    $probe.StartInfo.Arguments = 'info --format {{.ServerVersion}}'
    $probe.StartInfo.UseShellExecute = $false
    $probe.StartInfo.CreateNoWindow = $true
    $probe.StartInfo.RedirectStandardOutput = $true
    $probe.StartInfo.RedirectStandardError = $true
    try {
        [void]$probe.Start()
        $out = $probe.StandardOutput.ReadToEndAsync()
        [void]$probe.StandardError.ReadToEndAsync()
        if (-not $probe.WaitForExit(5000)) { $probe.Kill(); [void]$probe.WaitForExit(2000); return $false }
        return ($probe.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($out.Result))
    }
    catch { return $false }
    finally { $probe.Dispose() }
}

# Safety guards: never disturb sockets a live engine owns, and never delete
# under a backend process that is still running -- an engine that is merely
# not up yet is the normal state during Docker Desktop's own startup.
if (-not $Force) {
    if (Test-EngineReachable) {
        Write-Info 'Docker engine is reachable; leaving its live sockets untouched. Nothing to do.'
        exit 0
    }
    if (Get-Process -Name 'com.docker.backend' -ErrorAction SilentlyContinue) {
        Write-Info 'Docker backend process is running; reporting only, deleting nothing.'
        $ReportOnly = $true
    }
}

function Test-IsReparse($item) {
    return [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
}

$stuck = New-Object System.Collections.Generic.List[string]
$cleared = 0

foreach ($dir in $socketDirs) {
    if (-not (Test-Path -LiteralPath $dir)) { continue }
    $items = @(Get-ChildItem -LiteralPath $dir -Force -ErrorAction SilentlyContinue)
    foreach ($item in $items) {
        # Only files can be AF_UNIX sockets. A directory that refuses a plain
        # delete is not a stuck socket and must not be reported as one.
        if ($item.PSIsContainer) { continue }
        $path = $item.FullName
        if ($ReportOnly) {
            $stuck.Add($path)
            continue
        }
        # Best-effort in-place delete. Succeeds for plain / non-reparse leftovers
        # and anything the filter does not guard; a reparse socket the minifilter
        # blocks throws Win32 1920. Ground truth for "stuck" is existence AFTER
        # the attempt, not the reported attributes: the filter strips the
        # ReparsePoint bit from directory listings, so a stuck socket can
        # enumerate as an ordinary file. Never let a failure abort the sweep.
        try { Remove-Item -LiteralPath $path -Force -ErrorAction Stop } catch { }
        if (Test-Path -LiteralPath $path) {
            $stuck.Add($path)
        }
        else {
            $cleared++
            Write-Info ("Cleared stale socket: {0}" -f $path)
        }
    }
}

if ($cleared -eq 0 -and $stuck.Count -eq 0) {
    Write-Info 'No stale Docker sockets found.'
    exit 0
}

if ($ReportOnly) {
    # Nothing was probed, so nothing is known to be stuck: list what is present
    # with the driver status and leave the judgement to the reader.
    $acRunning = @()
    foreach ($svc in @('vgk', 'FACEIT', 'FACEIT_IOMMU')) {
        $s = Get-Service -Name $svc -ErrorAction SilentlyContinue
        if ($s -and $s.Status -eq 'Running') { $acRunning += $svc }
    }
    Write-Warning ("{0} socket file(s) are present under the Docker socket directories (not probed):" -f $stuck.Count)
    foreach ($p in $stuck) { Write-Warning ("  {0}" -f $p) }
    if ($acRunning.Count -gt 0) {
        Write-Warning ("Anti-cheat minifilter driver(s) running: {0}. If the engine never comes up, a stale reparse socket blocked by them is the likely cause." -f ($acRunning -join ', '))
    }
    Write-Host 'If Docker Desktop stays stuck: fully quit it (tray -> Quit Docker Desktop) or reboot, then run the launcher again. Do NOT reset or delete Docker data.' -ForegroundColor Yellow
    exit 3
}

if ($stuck.Count -eq 0) {
    Write-Info ("Cleared {0} stale socket(s). Docker Desktop can start cleanly." -f $cleared)
    exit 0
}

# Reparse-stuck: identify the anti-cheat drivers responsible and print the
# correct operational recovery, then signal the caller with exit code 3.
$acRunning = @()
foreach ($svc in @('vgk', 'FACEIT', 'FACEIT_IOMMU')) {
    $s = Get-Service -Name $svc -ErrorAction SilentlyContinue
    if ($s -and $s.Status -eq 'Running') { $acRunning += $svc }
}

Write-Warning ("{0} stale AF_UNIX socket(s) are stuck as reparse points and cannot be removed by this user:" -f $stuck.Count)
foreach ($p in $stuck) { Write-Warning ("  {0}" -f $p) }
if ($acRunning.Count -gt 0) {
    Write-Warning ("A kernel anti-cheat minifilter is blocking them (Win32 1920). Running driver(s): {0}." -f ($acRunning -join ', '))
}
else {
    Write-Warning 'A kernel filesystem minifilter is blocking them (Win32 1920).'
}
Write-Host ''
Write-Host 'Recovery (does NOT weaken security, does NOT touch your data):' -ForegroundColor Yellow
Write-Host '  1. Fully quit Docker Desktop (tray -> Quit Docker Desktop), or reboot.'
Write-Host '  2. Start Docker Desktop again, then re-run the launcher.'
Write-Host '  The stale socket is cleared during a clean re-initialisation.'
Write-Host '  Do NOT click "Reset to factory defaults" / delete Docker data -- that is'
Write-Host '  destructive and is not what this error requires.'
exit 3
