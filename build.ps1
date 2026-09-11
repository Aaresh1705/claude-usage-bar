# Builds dist\ClaudeUsageBar.exe - a single file that needs no Python on the
# machine it runs on.
#
#   powershell -ExecutionPolicy Bypass -File build.ps1
#
# The exe keeps config.json, the log and the cache beside itself, so put it in a
# folder of its own (Documents, not Program Files) or it will fall back to
# %LOCALAPPDATA%\claude-usage-bar.

param([switch]$KeepBuildDir)

$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $dir

function Find-Python {
    foreach ($candidate in @('python.exe', 'py.exe')) {
        $found = (Get-Command $candidate -ErrorAction SilentlyContinue).Source
        if ($found) { return $found }
    }
    throw 'Python 3 was not found on PATH. Install it from https://python.org or with: winget install Python.Python.3.12'
}

$python = Find-Python
Write-Host "Python: $python"

Write-Host 'Checking build dependencies...'
& $python -m pip install --quiet --disable-pip-version-check --upgrade pyinstaller pillow requests
if ($LASTEXITCODE -ne 0) { throw 'Could not install the build dependencies.' }

Write-Host 'Rendering the icon...'
& $python (Join-Path $dir 'make_icon.py')

Write-Host 'Building (this takes a minute)...'
$args = @(
    '-m', 'PyInstaller',
    '--noconfirm',
    '--onefile',
    '--windowed',                      # no console window
    '--name', 'ClaudeUsageBar',
    '--icon', (Join-Path $dir 'assets\ClaudeUsageBar.ico'),
    '--exclude-module', 'numpy',       # Pillow pulls these in if present
    '--exclude-module', 'scipy',
    '--exclude-module', 'matplotlib',
    (Join-Path $dir 'claude_usage_bar.pyw')
)
& $python @args
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }

if (-not $KeepBuildDir) {
    Remove-Item (Join-Path $dir 'build') -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $dir 'ClaudeUsageBar.spec') -Force -ErrorAction SilentlyContinue
}

$exe = Join-Path $dir 'dist\ClaudeUsageBar.exe'
$size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ''
Write-Host "Built $exe  ($size MB)"
Write-Host 'Copy it to any Windows 11 PC, put it in its own folder and run it.'
Write-Host 'For it to start with Windows, run:  install.ps1   (from the same folder)'
