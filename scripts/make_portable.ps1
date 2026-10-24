# Makes a zip of AIRTAC Advance to install on another PC: the code, settings and your library
# guide, WITHOUT your data, keys (.env), downloaded models or the .venv (the other PC builds its own).
#   powershell -ExecutionPolicy Bypass -File scripts\make_portable.ps1
# On the other PC: unzip anywhere, double-click Install-AIRTAC.cmd.
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path ([Environment]::GetFolderPath("Desktop")) ("AIRTAC-Advance-" + (Get-Date -Format "yyyyMMdd") + ".zip")
$skip = @(".venv", "data", "outputs", "workspace", "models", "backups", "__pycache__", ".pytest_cache")
$tmp = Join-Path $env:TEMP "airtac-portable"
if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
New-Item -ItemType Directory -Path $tmp | Out-Null
Get-ChildItem $root -Force | Where-Object { $skip -notcontains $_.Name -and $_.Name -ne ".env" } | ForEach-Object {
    Copy-Item $_.FullName -Destination $tmp -Recurse -Force
}
Get-ChildItem $tmp -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
# Your own books stay with you; only the guide and the sample note travel.
Get-ChildItem "$tmp\knowledge" -Recurse -File | Where-Object { $_.Name -ne "README.md" -and $_.Name -ne "sample-big-o.md" } | Remove-Item -Force
Compress-Archive -Path "$tmp\*" -DestinationPath $out -Force
Remove-Item $tmp -Recurse -Force
Write-Host "Made $out"
