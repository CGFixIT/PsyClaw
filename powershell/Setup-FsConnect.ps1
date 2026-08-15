<#
.SYNOPSIS
  Prepare %USERPROFILE%\CyClaw-FS and enable the confined list/stat/read profile.

.DESCRIPTION
  Windows twin of macos/setup-fsconnect.sh. Creates a confined jail directory,
  restricts ACLs to the current user (chmod 700 equivalent), and unless
  -PrepareOnly is set, runs macos/_enable_fsconnect_readlist.py so writes
  and indexing stay off.

.PARAMETER PrepareOnly
  Create the jail only; do not edit config.yaml.

.PARAMETER Config
  Path to config.yaml. Defaults to the sibling repo config, or
  $env:CYCLAW_FSCONNECT_CONFIG when set.
#>
[CmdletBinding()]
param(
    [switch]$PrepareOnly,
    [string]$Config = ""
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$msg) { Write-Host ("[cyclaw] " + $msg) }
function Write-Warn([string]$msg) { Write-Host ("[cyclaw] WARNING: " + $msg) -ForegroundColor Yellow }

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
if ($env:CYCLAW_FSCONNECT_CONFIG) {
    $ConfigPath = $env:CYCLAW_FSCONNECT_CONFIG
} elseif ($Config) {
    $ConfigPath = $Config
} else {
    $ConfigPath = Join-Path $RepoRoot "config.yaml"
}

$FsRoot = Join-Path $env:USERPROFILE "CyClaw-FS"
$ReadmePath = Join-Path $FsRoot "README.txt"

if (Test-Path -LiteralPath $FsRoot) {
    $item = Get-Item -LiteralPath $FsRoot -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        Write-Error "cyclaw: refusing non-directory or symlink fsconnect jail: $FsRoot"
        exit 1
    }
    if (-not $item.PSIsContainer) {
        Write-Error "cyclaw: refusing non-directory or symlink fsconnect jail: $FsRoot"
        exit 1
    }
} else {
    New-Item -ItemType Directory -Path $FsRoot | Out-Null
}

# chmod 700 equivalent: drop inheritance, current user only.
$acl = Get-Acl -LiteralPath $FsRoot
$acl.SetAccessRuleProtection($true, $false)
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$rule = New-Object Security.AccessControl.FileSystemAccessRule(
    $user,
    "FullControl",
    "ContainerInherit,ObjectInherit",
    "None",
    "Allow"
)
$acl.SetAccessRule($rule)
Set-Acl -LiteralPath $FsRoot -AclObject $acl

if (Test-Path -LiteralPath $ReadmePath) {
    $readmeItem = Get-Item -LiteralPath $ReadmePath -Force
    if (($readmeItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $readmeItem.PSIsContainer) {
        Write-Error "cyclaw: refusing non-file or symlink jail README: $ReadmePath"
        exit 1
    }
} else {
    @(
        "CyClaw read/list jail"
        ""
        "CyClaw is configured to list, stat, and read files only inside this folder."
        "Writes and indexing are off. Do not store secrets here if you later enable indexing."
    ) | Set-Content -LiteralPath $ReadmePath -Encoding utf8
}

Write-Step "fsconnect jail ready at $FsRoot"

if ($PrepareOnly) {
    Write-Step "prepare-only complete; config.yaml was not changed"
    exit 0
}

function Test-ConfigPython([string]$candidate) {
    if (-not $candidate) { return $false }
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if (-not $cmd -and -not (Test-Path -LiteralPath $candidate)) { return $false }
    & $candidate -c "import sys, yaml; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" 2>$null  # DevSkim: ignore DS104456 — call operator, not IEX
    return ($LASTEXITCODE -eq 0)
}

$python = $null
if ($env:CYCLAW_FSCONNECT_PYTHON -and (Test-ConfigPython $env:CYCLAW_FSCONNECT_PYTHON)) {
    $python = $env:CYCLAW_FSCONNECT_PYTHON
} else {
    $venvPy = Join-Path $env:USERPROFILE ".CyClaw\venv\Scripts\python.exe"
    foreach ($candidate in @($venvPy, "python", "py")) {
        if (Test-ConfigPython $candidate) {
            $python = $candidate
            break
        }
    }
}

if (-not $python) {
    Write-Error "cyclaw: Python 3.12+ with PyYAML is required to enable fsconnect safely."
    exit 1
}

$helper = Join-Path $RepoRoot "macos\_enable_fsconnect_readlist.py"
& $python $helper --config $ConfigPath --root $FsRoot  # DevSkim: ignore DS104456 — call operator, not IEX
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Step "list/stat/read enabled; writes and indexing remain off"
Write-Host "Next steps (run from the CyClaw repo):"
Write-Host "  python -m agentic.fsconnect.cli status"
Write-Host "  python -m agentic.fsconnect.cli list --root `"$FsRoot`""
exit 0
