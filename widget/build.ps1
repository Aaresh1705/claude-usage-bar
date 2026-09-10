# Builds the widget provider, assembles the MSIX layout and registers it with
# Windows. Loose-file registration needs Developer Mode (Settings > System >
# For developers); it needs no certificate and no administrator.
#
#   powershell -ExecutionPolicy Bypass -File build.ps1              build + register
#   powershell -ExecutionPolicy Bypass -File build.ps1 -Unregister  remove it again

param([switch]$Unregister, [switch]$SkipAssets)

$ErrorActionPreference = 'Stop'
$dir     = Split-Path -Parent $MyInvocation.MyCommand.Path
$project = Join-Path $dir 'ClaudeUsageWidget\ClaudeUsageWidget.csproj'
$package = Join-Path $dir 'package'
$payload = Join-Path $package 'ClaudeUsageWidget'
$appName = 'ClaudeUsageWidget'

if ($Unregister) {
    Get-AppxPackage $appName | Remove-AppxPackage
    Write-Host 'Widget provider unregistered.'
    return
}

# dotnet: prefer a machine-wide SDK, fall back to the per-user one.
$dotnet = (Get-Command dotnet.exe -ErrorAction SilentlyContinue).Source
if (-not $dotnet -or -not (& $dotnet --list-sdks)) { $dotnet = Join-Path $HOME '.dotnet\dotnet.exe' }
if (-not (Test-Path $dotnet)) { throw 'No .NET SDK found. Install the .NET 8 SDK first.' }

$devMode = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock' -ErrorAction SilentlyContinue).AllowDevelopmentWithoutDevLicense
if ($devMode -ne 1) {
    Write-Warning 'Developer Mode is off. Turn it on in Settings > System > For developers, then run this again.'
    Write-Warning 'Without it Windows refuses to register an unsigned package.'
    return
}

if (-not $SkipAssets) {
    $py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if ($py) { & $py (Join-Path $dir 'make_assets.py') }
}

Write-Host 'Publishing...'
if (Test-Path $payload) { Remove-Item $payload -Recurse -Force }
& $dotnet publish $project -c Release -r win-x64 --self-contained false -o $payload | Out-Null

# The manifest is the package identity; everything else is payload.
if (-not (Test-Path (Join-Path $package 'AppxManifest.xml'))) { throw 'AppxManifest.xml missing.' }

Write-Host 'Registering with Windows...'
Get-AppxPackage $appName | Remove-AppxPackage -ErrorAction SilentlyContinue
Add-AppxPackage -Register (Join-Path $package 'AppxManifest.xml') -ForceUpdateFromAnyVersion

$pkg = Get-AppxPackage $appName
Write-Host "Registered $($pkg.PackageFullName)"
Write-Host 'Open the widgets board (Win+W), choose "Add widgets", and pin "Claude usage".'
Write-Host "Provider log: $env:LOCALAPPDATA\claude-usage-bar\widget.log"
