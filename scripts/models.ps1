# Downloads the free models that fit this PC's memory (RAM). Run again any time to add more.
#   powershell -ExecutionPolicy Bypass -File scripts\models.ps1            (picks for your RAM)
#   powershell -ExecutionPolicy Bypass -File scripts\models.ps1 -All       (also the optional ones)
# Only one model is loaded at a time, so the sizes add up on disk, not in memory.
param([switch]$All)
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { Write-Host "Install Ollama first (scripts\install.ps1)."; exit 1 }
$ram = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
Write-Host "This PC has about $ram GB of RAM."

# general brain: NVIDIA Nemotron 3 Nano 4B (2.8 GB) on 6 GB or more; qwen3.5:2b on less
$general = if ($ram -ge 6) { "nemotron-3-nano:4b" } else { "qwen3.5:2b" }
$models = @($general, "nomic-embed-text")       # nomic-embed-text: meaning search in your library (0.3 GB)
if ($All) {
    $models += "qwen3.5:4b"                     # backup brain, also reads images (3.4 GB)
    $models += "qwen2.5-coder:1.5b"             # quick code answers (1 GB)
    $models += "hf.co/unsloth/medgemma-4b-it-GGUF:Q4_K_M"   # medicine, for learning only (2.5 GB)
    if ($ram -ge 16) { $models += "qwen2.5-coder:7b" }      # stronger code (4.7 GB)
}
foreach ($m in $models) {
    Write-Host "`nDownloading $m ..." -ForegroundColor Cyan
    ollama pull $m
}
Write-Host "`nDone. To use the optional ones, set them in config\airtac.toml under [brains], e.g."
Write-Host '  medical = "hf.co/unsloth/medgemma-4b-it-GGUF:Q4_K_M"'
Write-Host '  coding = "qwen2.5-coder:1.5b"'
if ($general -ne "nemotron-3-nano:4b") { Write-Host "`nAlso set [llm] model = `"$general`" in config\airtac.toml (less RAM here)." }
