<#
.SYNOPSIS
  Installs the CyClaw PowerShell coding harness for the current user.

.DESCRIPTION
  Target platforms: Windows 10, Windows 11, Windows Server 2019/2022 with
  Windows PowerShell 5.1 (also works on PowerShell 7+).

  After install, typing `cyclaw` in any PowerShell window starts the
  grok-build-style local coding harness (browser console on 127.0.0.1:8790).

  Everything mutable lives under %USERPROFILE%\.CyClaw:
    repo\       the CyClaw checkout (cloned, or linked via -RepoPath)
    venv\       the Python virtual environment
    bin\        the cyclaw.cmd shim + launcher
    sessions\   chat sessions with token tallies
    skills\     user-visible copy of .claude/skills
    tools\      connector/tool state
    memory\     harness memory log
    config.json selected model, soul on/off

.PARAMETER RepoPath
  Use an existing CyClaw clone instead of cloning from GitHub.

.PARAMETER SkipPythonDeps
  Create the home layout and shims but skip venv creation + pip installs
  (use when deps are already installed in an environment you will point to).

.PARAMETER NoProfileEdit
  Do not add the `cyclaw` function to the PowerShell profile. The
  %USERPROFILE%\.CyClaw\bin PATH entry is still added unless -NoPathEdit.

.PARAMETER NoPathEdit
  Do not modify the user PATH environment variable.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\Install-CyClaw.ps1

.EXAMPLE
  .\Install-CyClaw.ps1 -RepoPath C:\src\CyClaw
#>
[CmdletBinding()]
param(
    [string]$RepoPath = "",
    [switch]$SkipPythonDeps,
    [switch]$NoProfileEdit,
    [switch]$NoPathEdit
)

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/CGFixIT/CyClaw.git"

function Write-Step([string]$msg) { Write-Host ("[cyclaw] " + $msg) -ForegroundColor Cyan }
function Write-Warn([string]$msg) { Write-Host ("[cyclaw] WARNING: " + $msg) -ForegroundColor Yellow }

# -- 1. Home layout -----------------------------------------------------------
$Home_ = Join-Path $env:USERPROFILE ".CyClaw"
$Bin   = Join-Path $Home_ "bin"
$Repo  = Join-Path $Home_ "repo"
$Venv  = Join-Path $Home_ "venv"
foreach ($d in @($Home_, $Bin, (Join-Path $Home_ "sessions"), (Join-Path $Home_ "skills"),
                 (Join-Path $Home_ "tools"), (Join-Path $Home_ "memory"))) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null }
}
Write-Step "home layout ready at $Home_"

# -- 2. Repo ------------------------------------------------------------------
if ($RepoPath -ne "") {
    if (-not (Test-Path (Join-Path $RepoPath "harness\server.py"))) {
        throw "RepoPath '$RepoPath' does not look like a CyClaw checkout with the harness package."
    }
    $Repo = (Resolve-Path $RepoPath).Path
    Write-Step "using existing repo at $Repo"
}
elseif (-not (Test-Path (Join-Path $Repo "harness\server.py"))) {
    if (Test-Path $Repo) { Remove-Item -Recurse -Force $Repo }
    Write-Step "cloning CyClaw origin main to $Repo"
    & git clone --depth 1 $RepoUrl $Repo
    if ($LASTEXITCODE -ne 0) { throw "git clone failed (exit $LASTEXITCODE) — is git installed and GitHub reachable?" }
}
else {
    Write-Step "repo already present at $Repo (pulling latest main)"
    & git -C $Repo pull --ff-only
}

# -- 3. Python + dependencies ---------------------------------------------------
function Find-Python312 {
    # Prefer the py launcher with 3.12+, fall back to python on PATH.
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $vers = & py -0p 2>$null
        if ($vers -match "3\.(1[2-9]|[2-9][0-9])") { return @("py", "-3.12") }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        $v = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($v -and ([version]$v -ge [version]"3.12")) { return @("python") }
    }
    return $null
}

$PyCmd = Find-Python312
if ($null -eq $PyCmd) {
    Write-Warn "Python 3.12+ not found. Install it from https://www.python.org/downloads/ (tick 'Add to PATH'), then re-run this installer."
    if (-not $SkipPythonDeps) { throw "Python 3.12+ is required." }
}

if (-not $SkipPythonDeps) {
    $VenvPy = Join-Path $Venv "Scripts\python.exe"
    if (-not (Test-Path $VenvPy)) {
        Write-Step "creating virtual environment at $Venv"
        $PyArgs = @()
        if ($PyCmd.Count -gt 1) { $PyArgs += $PyCmd[1] }
        $PyArgs += @("-m", "venv", $Venv)
        & $PyCmd[0] @PyArgs
        if (-not (Test-Path $VenvPy)) { throw "venv creation failed." }
    }
    Write-Step "installing dependencies (CPU torch first, then requirements; this can take a few minutes)"
    & $VenvPy -m pip install --upgrade pip | Out-Null
    & $VenvPy -m pip install "torch==2.13.0+cpu" --index-url https://download.pytorch.org/whl/cpu
    if ($LASTEXITCODE -ne 0) { throw "torch install failed." }
    & $VenvPy -m pip install -r (Join-Path $Repo "requirements.txt") -c (Join-Path $Repo "constraints.txt") --ignore-installed PyYAML
    if ($LASTEXITCODE -ne 0) { throw "requirements install failed." }
    Write-Step "dependencies installed"
}

# -- 4. Launcher + shim ---------------------------------------------------------
$LauncherSrc = Join-Path $Repo "powershell\Invoke-CyClaw.ps1"
$LauncherDst = Join-Path $Bin "Invoke-CyClaw.ps1"
Copy-Item $LauncherSrc $LauncherDst -Force

$Shim = Join-Path $Bin "cyclaw.cmd"
$ShimBody = @"
@echo off
rem CyClaw harness launcher (installed shim). PowerShell 5.1+ required.
set "CYCLAW_HOME=%USERPROFILE%\.CyClaw"
set "CYCLAW_REPO=$Repo"
powershell -NoProfile -ExecutionPolicy Bypass -File "%USERPROFILE%\.CyClaw\bin\Invoke-CyClaw.ps1" %*
"@
Set-Content -Path $Shim -Value $ShimBody -Encoding ASCII
Write-Step "launcher shim written to $Shim"

# -- 5. User PATH -----------------------------------------------------------------
if (-not $NoPathEdit) {
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (($UserPath -split ";") -notcontains $Bin) {
        [Environment]::SetEnvironmentVariable("Path", ($UserPath.TrimEnd(";") + ";" + $Bin), "User")
        $env:Path = "$env:Path;$Bin"
        Write-Step "added $Bin to the user PATH (new windows inherit it)"
    }
}

# -- 6. PowerShell profile function ----------------------------------------------
if (-not $NoProfileEdit) {
    $ProfileDir = Split-Path $PROFILE.CurrentUserAllHosts
    if (-not (Test-Path $ProfileDir)) { New-Item -ItemType Directory -Path $ProfileDir -Force | Out-Null }
    $Marker = "# >>> cyclaw harness >>>"
    $Block = @"

$Marker
function global:cyclaw {
    `$env:CYCLAW_HOME = "`$env:USERPROFILE\.CyClaw"
    `$env:CYCLAW_REPO = "$Repo"
    & powershell -NoProfile -ExecutionPolicy Bypass -File "`$env:USERPROFILE\.CyClaw\bin\Invoke-CyClaw.ps1" @args
}
# <<< cyclaw harness <<<
"@
    $Existing = ""
    if (Test-Path $PROFILE.CurrentUserAllHosts) { $Existing = Get-Content $PROFILE.CurrentUserAllHosts -Raw }
    if ($Existing -notmatch [regex]::Escape($Marker)) {
        Add-Content -Path $PROFILE.CurrentUserAllHosts -Value $Block
        Write-Step "added 'cyclaw' function to $($PROFILE.CurrentUserAllHosts)"
    }
}

Write-Host ""
Write-Step "install complete. Open a NEW PowerShell window and run:  cyclaw"
Write-Step "the harness console opens at http://127.0.0.1:8790 — /help lists commands."
