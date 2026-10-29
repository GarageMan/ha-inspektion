<#
.SYNOPSIS
  Legt eine Desktop-Verknüpfung (und optional einen Autostart-Eintrag) an, die das
  Inspektions-Home-Assistant-Dashboard "Motorräder" als randloses Edge-App-Fenster öffnet.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\Install-InspektionDesktop.ps1 -Url "http://homeassistant.local:8123/dashboard-motorrad/motorraeder" -Autostart
#>
param(
    [string]$Url = "http://homeassistant.local:8123/dashboard-motorrad/motorraeder",
    [int]$Width = 440,
    [int]$Height = 760,
    [switch]$Autostart
)

$ErrorActionPreference = "Stop"

$edge = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge) { throw "Microsoft Edge wurde nicht gefunden." }

# Eigenes Profil: Fenstergröße/-position bleiben erhalten, getrennt vom normalen Browser
$profileDir = Join-Path $env:LOCALAPPDATA "InspektionHA\EdgeProfile"
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null

$appUrl = $Url
$edgeArgs = "--app=`"$appUrl`" --window-size=$Width,$Height --user-data-dir=`"$profileDir`" --no-first-run"

$shell = New-Object -ComObject WScript.Shell
function New-AppShortcut([string]$Path) {
    $lnk = $shell.CreateShortcut($Path)
    $lnk.TargetPath       = $edge
    $lnk.Arguments        = $edgeArgs
    $lnk.WorkingDirectory = Split-Path $edge
    $lnk.IconLocation     = "$edge,0"
    $lnk.Description      = "Inspektionstermine KTM & Husqvarna"
    $lnk.Save()
    Write-Host "Verknüpfung erstellt: $Path"
}

New-AppShortcut (Join-Path ([Environment]::GetFolderPath("Desktop")) "Inspektionen.lnk")

if ($Autostart) {
    New-AppShortcut (Join-Path ([Environment]::GetFolderPath("Startup")) "Inspektionen.lnk")
}

Write-Host "Fertig. Beim ersten Öffnen einmal bei Home Assistant anmelden ('Angemeldet bleiben'). Tipp: Fenster an den Bildschirmrand ziehen und per Rechtsklick in der Taskleiste anheften."
