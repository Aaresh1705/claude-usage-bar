# Installs Claude Usage Bar: starts it now and every time you sign in.
#
#   powershell -ExecutionPolicy Bypass -File install.ps1              install + start
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Update      git pull, then restart
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall   stop + remove (keeps config.json)
#
# It works either way round: next to ClaudeUsageBar.exe it uses the exe, and in
# a clone of the repository it runs the Python source with pythonw.

param([switch]$Uninstall, [switch]$Update, [switch]$NoStart)

$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'Claude Usage Bar.lnk'
$source = Join-Path $dir 'claude_usage_bar.pyw'
$icon = Join-Path $dir 'assets\ClaudeUsageBar.ico'

$exe = @((Join-Path $dir 'ClaudeUsageBar.exe'), (Join-Path $dir 'dist\ClaudeUsageBar.exe')) |
       Where-Object { Test-Path $_ } | Select-Object -First 1

function Stop-App {
    Get-CimInstance Win32_Process -Filter "Name='ClaudeUsageBar.exe'" -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*claude_usage_bar*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 400
}

if ($Uninstall) {
    Stop-App
    if (Test-Path $lnk) { Remove-Item $lnk -Force }
    Write-Host 'Claude Usage Bar removed. config.json and your log were left alone.'
    return
}

if ($Update) {
    if (-not (Test-Path (Join-Path $dir '.git'))) {
        throw 'This is not a clone of the repository, so there is nothing to pull. Download the latest exe instead.'
    }
    Write-Host 'Pulling the latest version...'
    git -C $dir pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw 'git pull failed - resolve it by hand, then run this again.' }
}

# --- how are we going to run it? ---------------------------------------------
if ($exe) {
    $target = $exe
    $arguments = ''
    Write-Host "Using $exe"
} else {
    if (-not (Test-Path $source)) { throw "Neither ClaudeUsageBar.exe nor claude_usage_bar.pyw is in $dir." }

    $pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
    if (-not $pythonw) {
        $python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
        if ($python) { $pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe' }
    }
    if (-not $pythonw -or -not (Test-Path $pythonw)) {
        throw @'
Python 3 was not found. Either install it:
    winget install Python.Python.3.12
or use the prebuilt ClaudeUsageBar.exe, which needs no Python.
'@
    }

    Write-Host 'Checking dependencies (pillow, requests)...'
    & $pythonw -c "import PIL, requests" 2>$null
    if ($LASTEXITCODE -ne 0) {
        & (Join-Path (Split-Path -Parent $pythonw) 'python.exe') -m pip install --quiet --disable-pip-version-check pillow requests
        if ($LASTEXITCODE -ne 0) { throw 'Could not install pillow/requests.' }
    }
    if (-not (Test-Path $icon)) {
        & (Join-Path (Split-Path -Parent $pythonw) 'python.exe') (Join-Path $dir 'make_icon.py') | Out-Null
    }

    $target = $pythonw
    $arguments = '"' + $source + '"'
    Write-Host "Using $pythonw"
}

# --- start with Windows -------------------------------------------------------
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$shortcut.TargetPath = $target
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $dir
$shortcut.WindowStyle = 7
$shortcut.Description = 'Claude usage on the taskbar'
if (Test-Path $icon) { $shortcut.IconLocation = $icon }
$shortcut.Save()
Write-Host "Starts with Windows: $lnk"

Stop-App
if (-not $NoStart) {
    if ($arguments) {
        Start-Process -FilePath $target -ArgumentList $arguments -WorkingDirectory $dir -WindowStyle Hidden
    } else {
        Start-Process -FilePath $target -WorkingDirectory $dir -WindowStyle Hidden
    }
    Write-Host 'Running. Look at the left end of your taskbar.'
    Write-Host ''
    Write-Host 'If the corner is occupied, turn the Widgets button off:'
    Write-Host '  Settings > Personalization > Taskbar > Widgets'
    Write-Host 'You also need to be signed in to Claude Code on this PC (run: claude).'
}
