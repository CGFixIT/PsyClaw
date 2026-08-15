<#
.SYNOPSIS
  Store one secret in Windows Credential Manager for CyClaw-CredMan-Env.ps1.

.DESCRIPTION
  Windows twin of macos/cyclaw-keychain-set.sh. Prompts on a TTY via
  Read-Host -AsSecureString and writes a GENERIC credential with CredWriteW.
  The secret is never a process argv token (the built-in cmd-key helper
  cannot store a password without putting it on the command line).

  Usage:
    powershell -File powershell\CyClaw-CredMan-Set.ps1 <target-name>

  Target names are labels, not secrets. Telegram generators default to
  "com.cgfixit.cyclaw.telegram-bot-token".

.NOTES
  Requires an interactive console. Fail-closed if stdin is redirected.
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

$account = $env:USERNAME
Write-Host "[cyclaw] Storing Credential Manager target '$Target' for account '$account'."
Write-Host "[cyclaw] You will be prompted for the secret value (input is not echoed)."
$secure = Read-Host "Secret" -AsSecureString
if ($null -eq $secure -or $secure.Length -eq 0) {
    Write-Error "cyclaw-credman-set: refusing to store an empty secret"
    exit 1
}

$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    $bytes = [Text.Encoding]::Unicode.GetBytes($plain)
    $plain = $null
    $ptr = [Runtime.InteropServices.Marshal]::AllocHGlobal($bytes.Length)
    try {
        [Runtime.InteropServices.Marshal]::Copy($bytes, 0, $ptr, $bytes.Length)
        for ($i = 0; $i -lt $bytes.Length; $i++) { $bytes[$i] = 0 }
        $cred = New-Object CyClawCredMan+CREDENTIAL
        $cred.Type = [CyClawCredMan]::CRED_TYPE_GENERIC
        $cred.TargetName = $Target
        $cred.UserName = $account
        $cred.CredentialBlobSize = $bytes.Length
        $cred.CredentialBlob = $ptr
        $cred.Persist = [CyClawCredMan]::CRED_PERSIST_LOCAL_MACHINE
        $cred.Comment = "CyClaw generated credential; read only via CyClaw-CredMan-Env.ps1"
        if (-not [CyClawCredMan]::CredWrite([ref]$cred, 0)) {
            $err = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            Write-Error "cyclaw-credman-set: CredWrite failed (win32=$err)"
            exit 1
        }
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        $bstr = [IntPtr]::Zero
        if ($ptr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::FreeHGlobal($ptr)
        }
    }
} catch {
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
    throw
}

Write-Host "[cyclaw] stored Credential Manager item: target=$Target account=$account"
exit 0
