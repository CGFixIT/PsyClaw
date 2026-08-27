<#
.SYNOPSIS
  Launches the CyClaw coding harness (installed by Install-CyClaw.ps1).

.DESCRIPTION
  Windows 10/11 + Server 2019/2022, Windows PowerShell 5.1 or PowerShell 7+.

  Starts the harness control plane on 127.0.0.1:8790 (loopback only) using the
  per-user venv under %USERPROFILE%\.CyClaw\venv and the repo at
  %CYCLAW_REPO% (or %USERPROFILE%\.CyClaw\repo), then opens the console in the
  default browser. Ctrl+C stops the server.

.PARAMETER Port
  Override the console port (default 8790; gate.py owns 8787).

.PARAMETER NoBrowser
  Do not open the browser; just serve.

.PARAMETER Repo
  Explicit path to the CyClaw checkout (overrides CYCLAW_REPO and the default).

.EXAMPLE
  cyclaw                 # via the installed shim / profile function
  .\Invoke-CyClaw.ps1 -NoBrowser -Port 8800
#>
[CmdletBinding()]
param(
    [int]$Port = $(if ($env:CYCLAW_HARNESS_PORT) { [int]$env:CYCLAW_HARNESS_PORT } else { 8790 }),
    [switch]$NoBrowser,
    [string]$Repo = ""
)

$ErrorActionPreference = "Stop"

$Home_ = if ($env:CYCLAW_HOME) { $env:CYCLAW_HOME } else { Join-Path $env:USERPROFILE ".CyClaw" }
if ($Repo -eq "") {
    $Repo = if ($env:CYCLAW_REPO) { $env:CYCLAW_REPO } else { Join-Path $Home_ "repo" }
}
$VenvPy = Join-Path $Home_ "venv\Scripts\python.exe"

if (-not (Test-Path (Join-Path $Repo "harness\server.py"))) {
    throw "CyClaw repo not found at '$Repo'. Run Install-CyClaw.ps1 first (or pass -Repo)."
}
if (-not (Test-Path $VenvPy)) {
    # Fall back to system python when the venv was skipped during install.
    $VenvPy = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $VenvPy) { throw "No venv at $Home_\venv and no python on PATH. Re-run Install-CyClaw.ps1." }
}

$env:CYCLAW_HOME = $Home_
$env:CYCLAW_REPO = $Repo
$env:CYCLAW_HARNESS_PORT = "$Port"
# CYCLAW_API_KEY is inherited from the caller; this launcher does not generate
# it and does not persist it. Unset means Soul/ops 401. Pasting the key into
# the browser field cannot set the server env.

Write-Host "[cyclaw] repo    : $Repo" -ForegroundColor Cyan
Write-Host "[cyclaw] home    : $Home_" -ForegroundColor Cyan
Write-Host "[cyclaw] console : http://127.0.0.1:$Port  (Ctrl+C to stop)" -ForegroundColor Cyan
if (-not $env:CYCLAW_API_KEY) {
    Write-Host "[cyclaw] warn    : CYCLAW_API_KEY not set - Soul / ops / harness state-changing routes will 401. Typing the key in the browser cannot configure the server; set the env var, then restart." -ForegroundColor Yellow
}

if (-not $NoBrowser) {
    # Open the browser slightly after the server starts; the page retries
    # until the API answers, so a race here is harmless.
    Start-Job -ScriptBlock {
        param($url)
        Start-Sleep -Seconds 2
        Start-Process $url
    } -ArgumentList "http://127.0.0.1:$Port" | Out-Null
}

Push-Location $Repo
try {
    & $VenvPy -m harness.server
}
finally {
    Pop-Location
}
