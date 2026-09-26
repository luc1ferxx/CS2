[CmdletBinding()]
param(
    [switch]$SkipRenderer,
    [switch]$PreflightOnly,
    [switch]$OpenBrowser
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$appCompose = Join-Path $projectRoot 'docker-compose.yml'
$localOverride = Join-Path $projectRoot '.local\qa\compose-loopback.yml'
$rendererCompose = Join-Path $projectRoot '.local\renderer\compose.yml'
$rendererEnv = Join-Path $projectRoot '.local\renderer\.env'
$rendererStart = Join-Path $projectRoot '.local\renderer\start.ps1'
$dashboardUrl = 'http://localhost:3000/dashboard'

function Require-File([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required local setup file is missing: $Path. Restore this checkout's local configuration before starting."
    }
}

function Test-DockerEngine([string]$DockerPath) {
    # Bound each CLI probe as well as the outer wait; a stalled backend can hang docker info.
    $probe = New-Object System.Diagnostics.Process
    $probe.StartInfo.FileName = $DockerPath
    $probe.StartInfo.Arguments = 'info --format {{.ServerVersion}}'
    $probe.StartInfo.UseShellExecute = $false
    $probe.StartInfo.CreateNoWindow = $true
    $probe.StartInfo.RedirectStandardOutput = $true
    $probe.StartInfo.RedirectStandardError = $true
    try {
        [void]$probe.Start()
        $stdout = $probe.StandardOutput.ReadToEndAsync()
        $stderr = $probe.StandardError.ReadToEndAsync()
        if (-not $probe.WaitForExit(5000)) {
            $probe.Kill()
            [void]$probe.WaitForExit(2000)
            return $false
        }
        return ($probe.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($stdout.Result))
    }
    finally { $probe.Dispose() }
}

function Start-DockerDesktop([string]$DesktopPath) {
    # Launch through Explorer, never as a child of this process. When this
    # launcher runs inside a packaged (MSIX) desktop app such as the Claude or
    # Codex desktop apps, a direct child inherits the app's file-system
    # virtualization, which cannot open Docker's AF_UNIX socket files; the
    # backend then crashes renaming a stale socket with Win32 1920 ("The file
    # cannot be accessed by the system"). Started by Explorer it runs in the
    # normal user session and clears stale sockets itself. From an ordinary
    # shell or a double-click this behaves exactly like a direct start.
    $explorer = Join-Path $env:WINDIR 'explorer.exe'
    for ($attempt = 0; $attempt -lt 2; $attempt++) {
        Start-Process -FilePath $explorer -ArgumentList ('"{0}"' -f $DesktopPath)
        # Explorer hands off and returns at once; confirm the app really started
        # (a new instance exits if an old one is still shutting down).
        $deadline = [DateTime]::UtcNow.AddSeconds(20)
        do {
            Start-Sleep -Seconds 2
            if (Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue) { return }
        } while ([DateTime]::UtcNow -lt $deadline)
    }
}

function Wait-DockerEngine([int]$Seconds) {
    # Require two answers 5 s apart: an instance that is quitting can still
    # answer once, and a fresh one can flap while its backend settles.
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        if (Test-DockerEngine $script:dockerPath) {
            Start-Sleep -Seconds 5
            if (Test-DockerEngine $script:dockerPath) { return $true }
        }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    return $false
}

function Stop-DockerDesktop {
    # Only called when the engine has been down for the whole wait, so no
    # container is running and a graceful quit would just block on the broken
    # backend (`docker desktop stop` can hang for minutes, and its helper
    # process retries later -- quitting the fresh instance we start next).
    # End the app, its backend and any CLI helper, then stop the engine VM.
    # Volumes, images and settings are never touched.
    $names = 'Docker Desktop', 'com.docker.backend', 'com.docker.build', 'docker-desktop'
    Get-Process -Name $names -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    & (Join-Path $env:WINDIR 'System32\wsl.exe') --terminate docker-desktop 2>$null | Out-Null
    # Do not start the next instance until the old one is gone; otherwise the
    # new app exits on the single-instance lock.
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ((Get-Process -Name $names -ErrorAction SilentlyContinue) -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Seconds 1
        Get-Process -Name $names -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 3
}

function Invoke-DockerStep([string]$Label, [string[]]$DockerArgs) {
    Write-Host $Label
    & $script:dockerPath @DockerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed (Docker exit $LASTEXITCODE). Existing containers and data have been preserved."
    }
}

function Wait-HttpReady([string]$Url, [switch]$HealthJson) {
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        try {
            if ($HealthJson) {
                $response = Invoke-RestMethod -Uri $Url -TimeoutSec 5
                if ($response.status -eq 'ok') { return }
            }
            else {
                $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
                if ($response.StatusCode -eq 200) { return }
            }
        }
        catch { }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Service did not become ready: $Url. Inspect docker compose logs; no data was removed."
}

try {
    Require-File $appCompose
    Require-File $localOverride
    if (-not $SkipRenderer) {
        Require-File $rendererCompose
        Require-File $rendererEnv
        Require-File $rendererStart
        Require-File (Join-Path $projectRoot '.local\renderer\run.ps1')
        Require-File (Join-Path $projectRoot '.venv\Scripts\python.exe')
    }
    $script:dockerPath = (Get-Command docker.exe -ErrorAction Stop).Source
    if ($PreflightOnly) {
        Write-Host "Local startup configuration found in $projectRoot. Preflight only; nothing was started."
        exit 0
    }

    Write-Host 'Checking Docker engine...'
    if (-not (Test-DockerEngine $script:dockerPath)) {
        $desktopPath = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
        Require-File $desktopPath
        if (-not (Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue)) {
            Write-Host 'Starting Docker Desktop...'
            Start-DockerDesktop $desktopPath
        }
        $engineReady = Wait-DockerEngine 120
        if (-not $engineReady) {
            # Docker Desktop is up but its engine is not: typically it was started
            # from inside a packaged app (see Start-DockerDesktop) or it crashed
            # on a stale socket. Restart it once in the normal user session.
            Write-Host 'Docker Desktop is running but its engine is down; restarting it once...'
            Stop-DockerDesktop
            Start-DockerDesktop $desktopPath
            $engineReady = Wait-DockerEngine 180
        }
        if (-not $engineReady) {
            throw "Docker engine did not start. See $env:LOCALAPPDATA\Docker\backend.error.json and the logs in $env:LOCALAPPDATA\Docker\log\host. No data was removed; do not reset Docker Desktop to factory defaults."
        }
    }

    $appArgs = @('compose', '--project-directory', $projectRoot, '-p', 'cs2', '-f', $appCompose, '-f', $localOverride)
    # --build keeps the containers on the checked-out code; unchanged sources hit
    # the build cache, so this costs seconds when nothing changed.
    Invoke-DockerStep 'Starting the CS2 Coach app...' ($appArgs + @('up', '-d', '--build', '--wait', '--wait-timeout', '120'))
    Wait-HttpReady 'http://localhost:8000/health' -HealthJson
    Wait-HttpReady $dashboardUrl

    if (-not $SkipRenderer) {
        $renderArgs = @('compose', '--project-directory', $projectRoot, '--env-file', $rendererEnv, '-p', 'cs2-renderer', '-f', $rendererCompose)
        Invoke-DockerStep 'Starting the recording database...' ($renderArgs + @('up', '-d', '--wait', '--wait-timeout', '120'))
        Invoke-DockerStep 'Checking the recording database...' ($renderArgs + @('exec', '-T', 'postgres', 'pg_isready', '-U', 'csdm', '-d', 'csdm', '-t', '5'))
        $shellPath = (Get-Process -Id $PID).Path
        & $shellPath -NoProfile -ExecutionPolicy Bypass -File $rendererStart
        if ($LASTEXITCODE -ne 0) {
            throw 'The app is available, but the recording worker did not start. Read the worker error above and .local/renderer/worker-error.log.'
        }
    }

    Write-Host "CS2 Coach is ready: $dashboardUrl"
    if ($OpenBrowser) { Start-Process $dashboardUrl }
    exit 0
}
catch {
    Write-Host ("Startup failed: " + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
