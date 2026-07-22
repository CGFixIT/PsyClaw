<#
.SYNOPSIS
  Removes the CyClaw harness integration from the current user's environment.

.DESCRIPTION
  Windows 10/11 + Server 2019/2022, Windows PowerShell 5.1 or PowerShell 7+.
  Removes the `cyclaw` profile function and the %USERPROFILE%\.CyClaw\bin PATH
  entry. The home directory (sessions, venv, repo clone) is KEPT by default so
  no data is lost; pass -RemoveHome to delete it (prompts first).

.EXAMPLE
  .\Uninstall-CyClaw.ps1              # keep ~/.CyClaw data
  .\Uninstall-CyClaw.ps1 -RemoveHome  # also delete ~/.CyClaw
#>
[CmdletBinding()]
param(
    [switch]$RemoveHome
)

$ErrorActionPreference = "Stop"
$Home_ = Join-Path $env:USERPROFILE ".CyClaw"
$Bin   = Join-Path $Home_ "bin"

# -- profile block --------------------------------------------------------------
$Marker = "# >>> cyclaw harness >>>"
if (Test-Path $PROFILE.CurrentUserAllHosts) {
    $text = Get-Content $PROFILE.CurrentUserAllHosts -Raw
    if ($text -match [regex]::Escape($Marker)) {
        $pattern = "(?s)\r?\n?" + [regex]::Escape($Marker) + ".*?# <<< cyclaw harness <<<"
        $cleaned = [regex]::Replace($text, $pattern, "")
        Set-Content -Path $PROFILE.CurrentUserAllHosts -Value $cleaned -Encoding UTF8
        Write-Host "[cyclaw] removed profile function from $($PROFILE.CurrentUserAllHosts)"
    }
}

# -- PATH entry -------------------------------------------------------------------
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath) {
    $entries = $UserPath -split ";" | Where-Object { $_ -ne $Bin -and $_ -ne "" }
    [Environment]::SetEnvironmentVariable("Path", ($entries -join ";"), "User")
    Write-Host "[cyclaw] removed $Bin from the user PATH"
}

# -- home directory -----------------------------------------------------------------
if ($RemoveHome -and (Test-Path $Home_)) {
    $answer = Read-Host "Delete $Home_ including all sessions and the venv? (y/N)"
    if ($answer -eq "y" -or $answer -eq "Y") {
        Remove-Item -Recurse -Force $Home_
        Write-Host "[cyclaw] removed $Home_"
    }
    else {
        Write-Host "[cyclaw] kept $Home_"
    }
}

Write-Host "[cyclaw] uninstall complete."
