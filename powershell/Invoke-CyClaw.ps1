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

# Match harness/server.py _MIN_USER_PORT/_MAX_PORT so a privileged or out-of-range
# override fails before we print a working-looking console URL.
if ($Port -lt 1024 -or $Port -gt 65535) {
    throw "Port must be between 1024 and 65535 (got $Port)"
}

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
# CYCLAW_API_KEY is inherited from the caller, or loaded below from
# %USERPROFILE%\.CyClaw\.env then the repo .env (Darwin twin:
# macos/invoke-cyclaw.sh). Browser paste cannot set the server env.

function Test-CyclawDotenvOwnerOnly([string]$Path) {
    try {
        $acl = Get-Acl -LiteralPath $Path
    } catch {
        return $false
    }
    foreach ($ace in $acl.Access) {
        if ($ace.AccessControlType -ne 'Allow') { continue }
        $id = $ace.IdentityReference.Value
        if ($id -eq 'Everyone' -or $id -eq 'BUILTIN\Users' -or $id -eq 'NT AUTHORITY\Authenticated Users') {
            return $false
        }
    }
    return $true
}

function Import-CyclawDotenv([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    if (-not (Test-CyclawDotenvOwnerOnly $Path)) {
        Write-Host "[cyclaw] warn    : refusing to source $Path (ACL is not owner-only; want current-user only). Fix with: icacls `"$Path`" /inheritance:r /grant:r `"${env:USERNAME}:(R,W)`"" -ForegroundColor Yellow
        return $false
    }
    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq '' -or $line.StartsWith('#')) { return }
        if ($line.StartsWith('export ')) { $line = $line.Substring(7).Trim() }
        $eq = $line.IndexOf('=')
        if ($eq -lt 1) { return }
        $name = $line.Substring(0, $eq).Trim()
        if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { return }
        $val = $line.Substring($eq + 1).Trim()
        if ($val.Length -ge 2 -and (($val.StartsWith("'") -and $val.EndsWith("'")) -or ($val.StartsWith('"') -and $val.EndsWith('"')))) {
            $val = $val.Substring(1, $val.Length - 2).Replace("'\''", "'")
        }
        Set-Item -Path ("Env:" + $name) -Value $val
    }
    return $true
}

if (-not $env:CYCLAW_API_KEY) {
    # Chained on the result, not existence: a refused HOME file must not shadow the repo copy.
    if (-not (Import-CyclawDotenv (Join-Path $Home_ ".env"))) {
        Import-CyclawDotenv (Join-Path $Repo ".env") | Out-Null
    }
}

Write-Host "[cyclaw] repo    : $Repo" -ForegroundColor Cyan
Write-Host "[cyclaw] home    : $Home_" -ForegroundColor Cyan
Write-Host "[cyclaw] console : http://127.0.0.1:$Port  (Ctrl+C to stop)" -ForegroundColor Cyan
if (-not $env:CYCLAW_API_KEY) {
    Write-Host "[cyclaw] warn    : CYCLAW_API_KEY not set - Soul / ops / harness state-changing routes will 401. Typing the key in the browser cannot configure the server; source $Home_\.env or set the env var, then restart." -ForegroundColor Yellow
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
    # Canonical telemetry/update-check block, set in THIS process so the
    # harness (and every child it spawns) inherits it before any interpreter
    # starts. Single source of truth: utils/telemetry_kill.py renders the
    # lines; nothing here hand-copies a key. Positioned after the .env import
    # above so canonical values overwrite any hostile dotenv value, mirroring
    # apply_telemetry_kill()'s own overwrite semantics. Non-fatal on failure:
    # every entry point re-applies the block at import anyway. Note this
    # cannot un-send THIS PowerShell host's own startup telemetry -- pwsh
    # reads POWERSHELL_TELEMETRY_OPTOUT once, at its own launch, which is why
    # the cmd shim written by Install-CyClaw.ps1 sets it before powershell
    # starts.
    # Parsed as DATA, never executed: only two rigid line shapes act (a
    # set-literal and a remove-literal over a validated env-var name), so a
    # compromised or garbled export cannot inject code the way piping it to
    # Invoke-Expression could (DevSkim DS104456).
    # -S -E: no site init in the helper interpreter (a venv sitecustomize/.pth
    # hook must not fire before the module emits the safe values) and no
    # ambient PYTHONPATH; the module is stdlib-only and repo-local.
    $killLines = & $VenvPy -S -E -m utils.telemetry_kill --export powershell 2>$null
    if ($LASTEXITCODE -eq 0 -and $killLines) {
        foreach ($line in @($killLines)) {
            if ($line -match "^\`$env:([A-Za-z_][A-Za-z0-9_]*) = '(.*)'$") {
                Set-Item -Path ("Env:" + $Matches[1]) -Value $Matches[2]
            } elseif ($line -match "^Remove-Item -ErrorAction SilentlyContinue Env:([A-Za-z_][A-Za-z0-9_]*)$") {
                Remove-Item -ErrorAction SilentlyContinue -Path ("Env:" + $Matches[1])
            }
        }
    } else {
        Write-Host "[cyclaw] warn    : could not export telemetry-kill block (children still self-apply at import)" -ForegroundColor Yellow
    }
    & $VenvPy -m harness.server
}
finally {
    Pop-Location
}
