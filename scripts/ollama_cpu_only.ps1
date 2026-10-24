# Run Ollama on the CPU only (Ollama's docs: "disable all Vulkan GPUs by setting OLLAMA_VULKAN=0").
# Why: on this laptop Ollama's Vulkan backend picks the AMD Radeon R5 M330 (2 GB) and its model runner
# crashes (exit 0xc0000005), so every answer failed. The i3's CPU is slower but works.
#   powershell -ExecutionPolicy Bypass -File "F:\AIRTAC Advance\scripts\ollama_cpu_only.ps1"
[Environment]::SetEnvironmentVariable("OLLAMA_VULKAN", "0", "User")
$env:OLLAMA_VULKAN = "0"
$models = [Environment]::GetEnvironmentVariable("OLLAMA_MODELS", "User")
if ($models) { $env:OLLAMA_MODELS = $models }
Get-Process "ollama app", "ollama" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 3
$app = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama app.exe"
if (Test-Path $app) { Start-Process $app } else { Start-Process "ollama" "serve" -WindowStyle Hidden }
Start-Sleep -Seconds 6
Write-Host "Ollama restarted on the CPU (OLLAMA_VULKAN=0). Models in: $env:OLLAMA_MODELS"
& ollama list
