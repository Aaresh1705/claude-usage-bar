# Installs Claude Usage Bar: creates a Startup shortcut and launches it now.
# Usage:  powershell -ExecutionPolicy Bypass -File install.ps1        (install)
#         powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall

param([switch]$Uninstall)

$ErrorActionPreference = 'Stop'
$dir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $dir 'claude_usage_bar.pyw'
$lnk    = Join-Path ([Environment]::GetFolderPath('Startup')) 'Claude Usage Bar.lnk'

if ($Uninstall) {
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -like '*claude_usage_bar.pyw*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    if (Test-Path $lnk) { Remove-Item $lnk -Force }
    Write-Host 'Claude Usage Bar removed (config.json kept).'
    return
}

# Locate pythonw.exe
$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pyw) {
    $py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if ($py) { $pyw = Join-Path (Split-Path -Parent $py) 'pythonw.exe' }
}
if (-not $pyw -or -not (Test-Path $pyw)) { throw 'pythonw.exe not found on PATH.' }

# Dependencies
& $pyw -c "import PIL, requests" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Installing dependencies (pillow, requests)...'
    & (Join-Path (Split-Path -Parent $pyw) 'python.exe') -m pip install --quiet pillow requests
}

# Startup shortcut
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath        = $pyw
$s.Arguments         = '"' + $script + '"'
$s.WorkingDirectory  = $dir
$s.WindowStyle       = 7
$s.Description       = 'Claude usage bar'
$s.Save()
Write-Host "Startup shortcut created: $lnk"

# Restart any running instance
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like '*claude_usage_bar.pyw*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep -Milliseconds 400
Start-Process -FilePath $pyw -ArgumentList ('"' + $script + '"') -WorkingDirectory $dir -WindowStyle Hidden
# Promote our tray icons out of the overflow. New tray icons default to hidden in
# Windows 11, and each segment is a separate icon, so they all need promoting once.
Start-Sleep -Seconds 3
$promoted = 0
Get-ChildItem 'HKCU:\Control Panel\NotifyIconSettings' -ErrorAction SilentlyContinue | ForEach-Object {
    $v = Get-ItemProperty $_.PSPath
    if ($v.ExecutablePath -eq $pyw) {
        New-ItemProperty -Path $_.PSPath -Name 'IsPromoted' -Value 1 -PropertyType DWord -Force | Out-Null
        $promoted++
    }
}
if ($promoted) {
    Write-Host "Promoted $promoted tray entries out of the overflow; restarting to apply..."
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -like '*claude_usage_bar.pyw*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Start-Sleep -Milliseconds 500
    Start-Process -FilePath $pyw -ArgumentList ('"' + $script + '"') -WorkingDirectory $dir -WindowStyle Hidden
}

Write-Host 'Running. Look for the usage pill in the tray (drag it out of the overflow if hidden).'
