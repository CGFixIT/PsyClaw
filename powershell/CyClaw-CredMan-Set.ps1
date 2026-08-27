<#
.SYNOPSIS
  Store one secret in Windows Credential Manager for CyClaw-CredMan-Env.ps1.

.DESCRIPTION
  Windows twin of macos/cyclaw-keychain-set.sh. Prompts on a TTY via
  Read-Host -AsSecureString and writes a GENERIC credential with CredWriteW.
  The secret is never a process argv token (the built-in cmdkey helper
  cannot store a password without putting it on the command line).

  Usage:
    powershell -File powershell\CyClaw-CredMan-Set.ps1 <target-name>

  Target names are labels, not secrets. Telegram generators default to
  "com.cgfixit.cyclaw.telegram-bot-token".

.NOTES
  Requires an interactive console. Fail-closed if stdin is redirected.

  Cleanup: one try/finally wipes the unmanaged CredWrite blob, ZeroFreeBSTR,
  and Disposes the SecureString. PowerShell 7+ also registers
  [Console]::CancelKeyPress so Ctrl+C cannot skip that finally (5.1 often
  aborts the pipeline without running it). The handler is not installed on
  5.1 — e.Cancel=$true there can hang the host.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Target
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Target)) {
    Write-Error "usage: CyClaw-CredMan-Set.ps1 <target-name>"
    exit 1
}

try {
    $redirected = [Console]::IsInputRedirected
} catch {
    $redirected = $false
}
if ($redirected) {
    Write-Error "cyclaw-credman-set: requires an interactive terminal (SecureString prompt)"
    exit 1
}

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class CyClawCredMan {
    public const int CRED_TYPE_GENERIC = 1;
    public const int CRED_PERSIST_LOCAL_MACHINE = 2;

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
    public static extern bool CredWrite(ref CREDENTIAL credential, int flags);
}
"@

# Script-scope so the PS7 CancelKeyPress handler can see live pointers.
$script:CyclawCredBstr = [IntPtr]::Zero
$script:CyclawCredPtr = [IntPtr]::Zero
$script:CyclawCredBlobSize = 0
$script:CyclawCredSecure = $null
$script:CyclawCredCleaned = $false
$script:CyclawCredCancelHandler = $null

function Invoke-CyclawCredCleanup {
    if ($script:CyclawCredCleaned) { return }
    $script:CyclawCredCleaned = $true
    if ($script:CyclawCredPtr -ne [IntPtr]::Zero) {
        for ($i = 0; $i -lt $script:CyclawCredBlobSize; $i++) {
            [Runtime.InteropServices.Marshal]::WriteByte($script:CyclawCredPtr, $i, 0)  # DevSkim: ignore DS104456 — wipe CredWrite blob before FreeHGlobal
        }
        [Runtime.InteropServices.Marshal]::FreeHGlobal($script:CyclawCredPtr)  # DevSkim: ignore DS104456
        $script:CyclawCredPtr = [IntPtr]::Zero
        $script:CyclawCredBlobSize = 0
    }
    if ($script:CyclawCredBstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($script:CyclawCredBstr)  # DevSkim: ignore DS104456
        $script:CyclawCredBstr = [IntPtr]::Zero
    }
    if ($null -ne $script:CyclawCredSecure) {
        $script:CyclawCredSecure.Dispose()
        $script:CyclawCredSecure = $null
    }
}

# PowerShell 7+: CancelKeyPress runs on Ctrl+C *before* the pipeline is torn
# down. e.Cancel=$true stops the default process-kill so cleanup can finish,
# then we exit 130 (128+SIGINT). Do not install this on Windows PowerShell
# 5.1 — treating Ctrl+C as cancelable input can hang that host, and its
# CancelKeyPress behavior is not the same as pwsh.
$script:UsePs7Cancel = $false
try {
    $script:UsePs7Cancel = (
        $PSVersionTable.PSVersion.Major -ge 7 -and
        [Environment]::UserInteractive -and
        -not [Console]::IsInputRedirected
    )
} catch {
    $script:UsePs7Cancel = $false
}
if ($script:UsePs7Cancel) {
    $script:CyclawCredCancelHandler = [ConsoleCancelEventHandler]{
        param($sender, $eventArgs)
        $eventArgs.Cancel = $true
        Invoke-CyclawCredCleanup
        [Environment]::Exit(130)
    }
    [Console]::add_CancelKeyPress($script:CyclawCredCancelHandler)
}

$account = $env:USERNAME
Write-Host "[cyclaw] Storing Credential Manager target '$Target' for account '$account'."
Write-Host "[cyclaw] You will be prompted for the secret value (input is not echoed)."

$exitCode = 1
try {
    $script:CyclawCredSecure = Read-Host "Secret" -AsSecureString
    if ($null -eq $script:CyclawCredSecure -or $script:CyclawCredSecure.Length -eq 0) {
        Write-Error "cyclaw-credman-set: refusing to store an empty secret"
        throw "empty secret"
    }

    $script:CyclawCredBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($script:CyclawCredSecure)  # DevSkim: ignore DS104456 — TTY SecureString to CredWrite blob; never argv
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($script:CyclawCredBstr)  # DevSkim: ignore DS104456
    $bytes = [Text.Encoding]::Unicode.GetBytes($plain)
    $plain = $null
    $script:CyclawCredBlobSize = $bytes.Length
    $script:CyclawCredPtr = [Runtime.InteropServices.Marshal]::AllocHGlobal($script:CyclawCredBlobSize)  # DevSkim: ignore DS104456
    [Runtime.InteropServices.Marshal]::Copy($bytes, 0, $script:CyclawCredPtr, $script:CyclawCredBlobSize)  # DevSkim: ignore DS104456
    for ($i = 0; $i -lt $bytes.Length; $i++) { $bytes[$i] = 0 }

    $cred = New-Object CyClawCredMan+CREDENTIAL
    $cred.Type = [CyClawCredMan]::CRED_TYPE_GENERIC
    $cred.TargetName = $Target
    $cred.UserName = $account
    $cred.CredentialBlobSize = $script:CyclawCredBlobSize
    $cred.CredentialBlob = $script:CyclawCredPtr
    $cred.Persist = [CyClawCredMan]::CRED_PERSIST_LOCAL_MACHINE
    $cred.Comment = "CyClaw generated credential; read only via CyClaw-CredMan-Env.ps1"
    if (-not [CyClawCredMan]::CredWrite([ref]$cred, 0)) {
        $err = [Runtime.InteropServices.Marshal]::GetLastWin32Error()  # DevSkim: ignore DS104456
        Write-Error "cyclaw-credman-set: CredWrite failed (win32=$err)"
        throw "CredWrite failed"
    }
    $exitCode = 0
} catch {
    if ($exitCode -eq 0) { $exitCode = 1 }
    if ($_.Exception.Message -ne "empty secret" -and $_.Exception.Message -ne "CredWrite failed") {
        throw
    }
} finally {
    Invoke-CyclawCredCleanup
    if ($null -ne $script:CyclawCredCancelHandler) {
        try { [Console]::remove_CancelKeyPress($script:CyclawCredCancelHandler) } catch { }
        $script:CyclawCredCancelHandler = $null
    }
}

if ($exitCode -eq 0) {
    Write-Host "[cyclaw] stored Credential Manager item: target=$Target account=$account"
}
exit $exitCode
