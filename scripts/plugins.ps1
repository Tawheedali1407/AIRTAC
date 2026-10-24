# Installs the extra Python packages a plugin needs, into AIRTAC's own .venv.
#   powershell -ExecutionPolicy Bypass -File scripts\plugins.ps1 voice      (private speech on this PC)
#   powershell -ExecutionPolicy Bypass -File scripts\plugins.ps1 <plugin>   (reads plugins\<plugin>\manifest.toml)
param([Parameter(Mandatory = $true)][string]$Name)
$root = Split-Path -Parent $PSScriptRoot
$venv = "$root\.venv\Scripts\python.exe"
if (-not (Test-Path $venv)) { Write-Host "Run scripts\install.ps1 first."; exit 1 }
if ($Name -eq "voice") {
    & $venv -m pip install -r "$root\requirements-voice.txt"
    Write-Host "Now set [voice] stt = `"local`" (and tts = `"local`" with a Piper voice) in config\airtac.toml."
    Write-Host "Piper voices: https://huggingface.co/rhasspy/piper-voices (save the .onnx and .onnx.json in models\piper)."
    exit 0
}
$manifest = "$root\plugins\$Name\manifest.toml"
if (-not (Test-Path $manifest)) { Write-Host "No plugin called $Name."; exit 1 }
$pips = & $venv -c "import tomllib,sys; r=tomllib.load(open(sys.argv[1],'rb')).get('requires',{}); print(' '.join(r.get('pip', r.get('imports', []))))" $manifest
if (-not $pips.Trim()) { Write-Host "$Name needs no extra packages."; exit 0 }
& $venv -m pip install $pips.Split(" ")
Write-Host "Restart AIRTAC to turn $Name on."
