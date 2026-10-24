# Installs AIRTAC Advance on this PC. Safe to run again (it only adds what's missing).
#   Double-click Install-AIRTAC.cmd, or:  powershell -ExecutionPolicy Bypass -File scripts\install.ps1
# What it does, in order (it asks before anything that installs software for the whole PC):
#   1. finds Python 3.11 or newer,
#   2. makes a private Python folder (.venv) here and installs AIRTAC's packages into it,
#   3. makes .env from .env.example (keys are easier on the Connections page: they go to Windows Credential Manager),
#   4. checks for Ollama (the program that runs the AI models) and offers to install it,
#   5. downloads the models that fit your RAM (scripts\models.ps1),
#   6. puts AIRTAC on your desktop and Start menu (and, if you want, opens it when you sign in).
param([switch]$NoModels, [switch]$AtLogin)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
function Say($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Ask($question) { return (Read-Host "$question [y/n]") -match "^(y|yes)$" }

Say "1/6 Python"
$python = $null
foreach ($cand in @("py -3.14", "py -3.13", "py -3.12", "py -3.11", "python")) {
    $parts = $cand.Split(" "); $exe = $parts[0]; $rest = @($parts | Select-Object -Skip 1)
    try {
        $v = & $exe @rest -c "import sys; print(sys.version_info >= (3, 11))" 2>$null
        if ($v -eq "True") { $python = $cand; break }
    } catch { }
}
if (-not $python) {
    Write-Host "Python 3.11 or newer wasn't found. Install it from https://www.python.org/downloads/ (tick 'Add to PATH'), then run this again."
    if (Ask "Install Python 3.13 now with winget?") { winget install -e --id Python.Python.3.13; Write-Host "Close this window and run the installer again." }
    exit 1
}
Write-Host "Using $python"

Say "2/6 AIRTAC's own Python folder (.venv) and packages"
if (-not (Test-Path "$root\.venv\Scripts\python.exe")) {
    $parts = $python.Split(" "); $exe = $parts[0]; $rest = @($parts | Select-Object -Skip 1)
    & $exe @rest -m venv "$root\.venv"
}
$venv = "$root\.venv\Scripts\python.exe"
& $venv -m pip --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip inside .venv is broken; repairing it from Python's own copy..."
    & $venv "$root\scripts\repair_pip.py"
}
& $venv -m pip install -r "$root\requirements.txt"
$pipOk = ($LASTEXITCODE -eq 0)
if (-not $pipOk) { Write-Host "Some packages didn't install (see the lines above). AIRTAC still starts; it names what's missing." -ForegroundColor Yellow }

Say "3/6 Settings"
if (-not (Test-Path "$root\.env")) {
    New-Item "$root\.env" -ItemType File -Force | Out-Null
    Write-Host "Made .env. Add keys (all optional) and a phone passcode later on AIRTAC's Connections page."
} else { Write-Host ".env is already there (kept as it is)." }

Say "4/6 Ollama (runs the AI models on this PC)"
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    Write-Host "Ollama isn't installed. It's free and open source (MIT): https://ollama.com"
    if (Ask "Install Ollama now with winget?") {
        winget install -e --id Ollama.Ollama
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
        $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    }
}
if ($ollama) {
    # One model in memory at a time: right for 8 GB of RAM.
    [Environment]::SetEnvironmentVariable("OLLAMA_MAX_LOADED_MODELS", "1", "User")
    Write-Host "Ollama is ready."
} else { Write-Host "Skipped. AIRTAC will run on keyword rules until Ollama is installed." }

Say "5/6 Models"
if ($ollama -and -not $NoModels) { & powershell -NoProfile -ExecutionPolicy Bypass -File "$root\scripts\models.ps1" }
else { Write-Host "Skipped. Run scripts\models.ps1 any time." }

Say "6/6 Shortcuts"
$extra = @(); if ($AtLogin -or (Ask "Start AIRTAC (hidden, no window) when you sign in to Windows? Then it is always ready.")) { $extra = @("-AtLogin") }
& powershell -NoProfile -ExecutionPolicy Bypass -File "$root\scripts\make_shortcut.ps1" @extra

Say "Checking everything"
& $venv "$root\scripts\check_system.py"
Write-Host "`nDone. Double-click 'AIRTAC Advance' on your desktop to open it." -ForegroundColor Green
