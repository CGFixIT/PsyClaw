<#
.SYNOPSIS
  Fetch one Credential Manager secret and inject it as an environment variable.

.DESCRIPTION
  Windows twin of macos/cyclaw-keychain-env.sh. Generated scheduled tasks
  (trash-empty-task / poll-task / health-task / generate_service_task.py)
  put this in front of the real command so a token never lands in task XML.

  Usage:
    powershell -NoProfile -File powershell\CyClaw-CredMan-Env.ps1 <target> <ENV_VAR> -- <command> [args...]

  Composable: chain invocations to inject multiple secrets. Each layer
  exports one variable, then execs the next layer (or the real command).

  Fails closed: exits 1 without launching the wrapped command if the
  credential is missing or empty.

.NOTES
  CredReadW is the Windows API; `cmdkey` is never used (it cannot retrieve
  the password blob in a fail-closed way without leaking it).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Target,
    [Parameter(Mandatory = $true, Position = 1)]
    [string]$EnvVar
)

$ErrorActionPreference = "Stop"

# Remaining argv after the first two positional params, including a required "--".
$rest = @()
$sawSeparator = $false
foreach ($arg in $args) {
    if (-not $sawSeparator) {
        if ($arg -eq "--") {
            $sawSeparator = $true
            continue
        }
        Write-Error "usage: CyClaw-CredMan-Env.ps1 <target> <ENV_VAR> -- <command> [args...]"
        exit 1
    }
    $rest += $arg
}

if (-not $sawSeparator -or $rest.Count -lt 1) {
    Write-Error "usage: CyClaw-CredMan-Env.ps1 <target> <ENV_VAR> -- <command> [args...]"
    exit 1
}

if ($EnvVar -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
    Write-Error "cyclaw-credman-env: refusing invalid environment variable name: $EnvVar"
    exit 1
}

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class CyClawCredManRead {
    public const int CRED_TYPE_GENERIC = 1;

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct CREDENTIAL {
        public int Flags;
        public int Type;
        public string TargetName;
        public string Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public int CredentialBlobSize;
        public IntPtr CredentialBlob;
        public int Persist;
        public int AttributeCount;
        public IntPtr Attributes;
        public string TargetAlias;
        public string UserName;
    }

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool CredRead(string target, int type, int flags, out IntPtr credentialPtr);

    [DllImport("advapi32.dll", SetLastError = true)]
    public static extern void CredFree(IntPtr buffer);
}
"@

$credPtr = [IntPtr]::Zero
$secret = $null
try {
    if (-not [CyClawCredManRead]::CredRead($Target, [CyClawCredManRead]::CRED_TYPE_GENERIC, 0, [ref]$credPtr)) {
        $err = [Runtime.InteropServices.Marshal]::GetLastWin32Error()  # DevSkim: ignore DS104456 — CredRead last-error; cmdkey cannot retrieve the blob
        Write-Error "cyclaw-credman-env: no Credential Manager item for target '$Target' (win32=$err)"
        Write-Error "cyclaw-credman-env: store it first: powershell -File powershell\CyClaw-CredMan-Set.ps1 '$Target'"
        exit 1
    }
    $cred = [Runtime.InteropServices.Marshal]::PtrToStructure($credPtr, [type][CyClawCredManRead+CREDENTIAL])  # DevSkim: ignore DS104456 — CredRead unmanaged CREDENTIAL; not shellcode
    if ($cred.CredentialBlob -eq [IntPtr]::Zero -or $cred.CredentialBlobSize -le 0) {
        Write-Error "cyclaw-credman-env: Credential Manager item for target '$Target' is empty"
        exit 1
    }
    $secret = [Runtime.InteropServices.Marshal]::PtrToStringUni($cred.CredentialBlob, [int]($cred.CredentialBlobSize / 2))  # DevSkim: ignore DS104456 — CredRead blob to managed string; wiped after env inject
} finally {
    if ($credPtr -ne [IntPtr]::Zero) {
        [CyClawCredManRead]::CredFree($credPtr)
    }
}

if ([string]::IsNullOrEmpty($secret)) {
    Write-Error "cyclaw-credman-env: Credential Manager item for target '$Target' is empty"
    exit 1
}

Set-Item -Path "Env:$EnvVar" -Value $secret
$secret = $null

$exe = $rest[0]
$exeArgs = @()
if ($rest.Count -gt 1) {
    $exeArgs = $rest[1..($rest.Count - 1)]
}

# Inherit this process's environment (including the injected secret).
# Call operator — not Start-Process — so argv is not re-quoted through cmd.
& $exe @exeArgs  # DevSkim: ignore DS104456 — call operator, not IEX; argv not re-quoted through cmd
if ($null -ne $LASTEXITCODE) {
    exit $LASTEXITCODE
}
exit 0
