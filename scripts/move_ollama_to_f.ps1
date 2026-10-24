# Move Ollama's models from C: to F: (C: is full), the way Ollama's docs describe:
# quit Ollama, set OLLAMA_MODELS for your account, start Ollama again (https://docs.ollama.com/windows).
#
#   powershell -ExecutionPolicy Bypass -File "F:\AIRTAC Advance\scripts\move_ollama_to_f.ps1"
#
# What it does, in order:
#   1. copies Ollama's own log (C:\Users\<you>\AppData\Local\Ollama\server.log) to tmp\ollama-server.log,
#      so the reason for its errors can be read;
#   2. quits Ollama;
#   3. moves C:\Users\<you>\.ollama\models to F:\Ollama\models (robocopy; files leave C: only after they're on F:);
#   4. sets OLLAMA_MODELS=F:\Ollama\models for your account only (not system-wide);
#   5. starts Ollama again and lists the models, then shows free space on C: and F:.
# It doesn't move the Ollama program (about 2 GB under AppData\Local\Programs\Ollama). To move that too,
# uninstall Ollama and run OllamaSetup.exe /DIR="F:\Ollama\app" (from Ollama's Windows docs).
param([string]$Target = "F:\Ollama\models")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $env:USERPROFILE ".ollama\models"

New-Item -ItemType Directory -Force (Join-Path $root "tmp") | Out-Null
$log = Join-Path $env:LOCALAPPDATA "Ollama\server.log"
if (Test-Path $log) {
    Copy-Item $log (Join-Path $root "tmp\ollama-server.log") -Force
    Write-Host "1. Copied Ollama's log to tmp\ollama-server.log"
}

Write-Host "2. Quitting Ollama..."
Get-Process "ollama app", "ollama" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 3

New-Item -ItemType Directory -Force $Target | Out-Null
if ((Test-Path $src) -and ((Resolve-Path $src).Path -ne (Resolve-Path $Target).Path)) {
    Write-Host "3. Moving models from $src to $Target (a few minutes for 3 GB)..."
    robocopy $src $Target /E /MOVE /R:1 /W:1 /NFL /NDL /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy stopped with code $LASTEXITCODE. Files that weren't copied are still on C:." }
} else {
    Write-Host "3. Models are already at $Target"
}

[Environment]::SetEnvironmentVariable("OLLAMA_MODELS", $Target, "User")
$env:OLLAMA_MODELS = $Target
Write-Host "4. OLLAMA_MODELS = $Target (your account)"

$app = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama app.exe"
if (Test-Path $app) { Start-Process $app } else { Start-Process "ollama" "serve" -WindowStyle Hidden }
Start-Sleep -Seconds 6
Write-Host "5. Ollama started. Models it sees:"
& ollama list
Get-PSDrive C, F | Select-Object Name, @{n = "Free GB"; e = { [math]::Round($_.Free / 1GB, 1) } } | Format-Table -AutoSize
Write-Host "Done. Ask AIRTAC something to check."
