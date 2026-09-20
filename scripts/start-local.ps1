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

function Invoke-DockerSocketPreflight([switch]$ReportOnly) {
    # Clear stale AF_UNIX sockets a previous shutdown left under %LOCALAPPDATA%
    # (and surface the anti-cheat-minifilter cause when a socket is stuck) so
    # Docker's secrets-engine does not crash on its startup rename. Best-effort:
    # a failure here must never abort the launch attempt. Only ever runs while
    # the engine is down. See evict-stale-docker-sockets.ps1 for the full story.
    $evictScript = Join-Path $PSScriptRoot 'evict-stale-docker-sockets.ps1'
    if (-not (Test-Path -LiteralPath $evictScript -PathType Leaf)) {
        Write-Warning "Docker socket preflight script missing: $evictScript"
        return
    }
    try {
        if ($ReportOnly) { & $evictScript -ReportOnly } else { & $evictScript }
    }
    catch { Write-Warning ('Docker socket preflight error: ' + $_.Exception.Message) }
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
        $dockerDesktop = Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue
        if (-not $dockerDesktop) {
            # Engine down and Docker Desktop not running: clear any stale sockets
            # from a prior shutdown before the fresh start so the secrets-engine
            # rename cannot hit Win32 1920 and crash the backend.
            Invoke-DockerSocketPreflight
            $desktopPath = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
            Require-File $desktopPath
            Write-Host 'Starting Docker Desktop...'
            Start-Process -FilePath $desktopPath -WindowStyle Hidden | Out-Null
        }
        $engineDeadline = [DateTime]::UtcNow.AddSeconds(120)
        $engineReady = $false
        do {
            Start-Sleep -Seconds 2
            $engineReady = Test-DockerEngine $script:dockerPath
        } while (-not $engineReady -and [DateTime]::UtcNow -lt $engineDeadline)
        if (-not $engineReady) {
            # Still down after the wait. Docker Desktop is running now, so any
            # socket present may belong to its backend coming up: report what is
            # there and the driver status, but delete nothing.
            Invoke-DockerSocketPreflight -ReportOnly
            throw 'Docker Desktop is open but its engine is unavailable after 120 seconds. If the preflight above reported stale anti-cheat-blocked sockets, fully quit Docker Desktop (or reboot) and run this launcher again. Do not reset or delete its data.'
        }
    }

    $appArgs = @('compose', '--project-directory', $projectRoot, '-p', 'cs2', '-f', $appCompose, '-f', $localOverride)
    Invoke-DockerStep 'Starting the CS2 Coach app...' ($appArgs + @('up', '-d', '--wait', '--wait-timeout', '120'))
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
