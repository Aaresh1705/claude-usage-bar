# Installs Claude Usage Bar: starts it now and every time you sign in.
#
#   powershell -ExecutionPolicy Bypass -File install.ps1              install + start
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Update      git pull, then restart
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall   stop + remove (keeps config.json)
#
# It works either way round: in a clone it runs the Python source with pythonw,
# and where only ClaudeUsageBar.exe was copied it uses that. Add -Exe to prefer
# the exe even in a clone.

param([switch]$Uninstall, [switch]$Update, [switch]$NoStart, [switch]$Exe, [switch]$UseStartupFolder)

$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'Claude Usage Bar.lnk'
$taskName = 'Claude Usage Bar'
$source = Join-Path $dir 'claude_usage_bar.pyw'
$icon = Join-Path $dir 'assets\ClaudeUsageBar.ico'

# The source wins when it is there: a clone is meant to run from source, and on
# a machine with Windows Defender Application Control enforced an unsigned exe
# cannot start at all. -Exe forces the exe when you want to test it.
# (The variable is $exePath, not $exe: PowerShell variable names are
# case-insensitive, so $exe and the -Exe switch would be the same variable.)
$exePath = $null
if ($Exe -or -not (Test-Path $source)) {
    $exePath = @((Join-Path $dir 'ClaudeUsageBar.exe'), (Join-Path $dir 'dist\ClaudeUsageBar.exe')) |
           Where-Object { Test-Path $_ } | Select-Object -First 1
}

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
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
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
if ($exePath) {
    $target = $exePath
    $arguments = ''
    Write-Host "Using $exePath"
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
# A scheduled task rather than a Startup shortcut. The Startup folder is the
# last thing the shell gets to: measured on this machine, 170 seconds after
# boot, behind Teams, OneDrive, Spotify and a Java updater - long enough that
# it looks like it never started. A logon task does not queue behind them.
function Install-Shortcut {
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    $shortcut.TargetPath = $target
    $shortcut.Arguments = $arguments
    $shortcut.WorkingDirectory = $dir
    $shortcut.WindowStyle = 7
    $shortcut.Description = 'Claude usage on the taskbar'
    if (Test-Path $icon) { $shortcut.IconLocation = $icon }
    $shortcut.Save()
    Write-Host "Starts with Windows (Startup folder): $lnk"
}

$installed = $false
if (-not $UseStartupFolder) {
    try {
        $action = if ($arguments) {
            New-ScheduledTaskAction -Execute $target -Argument $arguments -WorkingDirectory $dir
        } else {
            New-ScheduledTaskAction -Execute $target -WorkingDirectory $dir
        }
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
        $trigger.Delay = 'PT10S'      # let the shell draw a taskbar to sit on
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -StartWhenAvailable `
            -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
            -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
            -MultipleInstances IgnoreNew
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
            -Settings $settings -Description 'Claude usage on the taskbar' -Force | Out-Null
        # One mechanism only: the app refuses to run twice anyway, but a stale
        # shortcut would keep starting the old location after a move.
        if (Test-Path $lnk) { Remove-Item $lnk -Force }
        Write-Host "Starts at sign-in (scheduled task '$taskName')"
        $installed = $true
    } catch {
        Write-Warning "Could not register the scheduled task ($($_.Exception.Message.Trim()))."
        Write-Warning 'Falling back to the Startup folder, which can start a couple of minutes late.'
    }
}
if (-not $installed) { Install-Shortcut }

Stop-App
if (-not $NoStart) {
    try {
        if ($arguments) {
            Start-Process -FilePath $target -ArgumentList $arguments -WorkingDirectory $dir -WindowStyle Hidden
        } else {
            Start-Process -FilePath $target -WorkingDirectory $dir -WindowStyle Hidden
        }
    } catch {
        $guard = 0
        try {
            $guard = (Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard `
                      -ClassName Win32_DeviceGuard).CodeIntegrityPolicyEnforcementStatus
        } catch { }
        if ($exePath -and $guard -eq 2) {
            throw @'
Windows refused to start the exe. This machine runs Windows Defender Application
Control in enforcement mode, which will not launch an unsigned executable from
any user-writable folder, wherever you put it. Use the source instead: clone the
repository and run install.ps1 there without -Exe (Python is signed, so it runs).
'@
        }
        throw
    }
    Write-Host 'Running. Look at the left end of your taskbar.'
    Write-Host ''
    Write-Host 'If the corner is occupied, turn the Widgets button off:'
    Write-Host '  Settings > Personalization > Taskbar > Widgets'
    Write-Host 'You also need to be signed in to Claude Code on this PC (run: claude).'
}
