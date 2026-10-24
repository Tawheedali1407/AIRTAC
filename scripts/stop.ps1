# Stops AIRTAC Advance: the server start.ps1 started, one run by hand with uvicorn, and (since 18 Oct) whatever
# Python program still holds AIRTAC's port, so a restart always really restarts.
$root = Split-Path -Parent $PSScriptRoot
$Port = 8765
$line = Select-String -Path "$root\config\airtac.toml" -Pattern '^\s*port\s*=\s*(\d+)' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($line) { $Port = [int]$line.Matches[0].Groups[1].Value }
$procs = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -like "*uvicorn*airtac.main:app*" -or $_.CommandLine -like "*scripts\serve.py*" })
$owner = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($owner) {
    $p = Get-CimInstance Win32_Process -Filter "ProcessId = $($owner.OwningProcess)"
    if ($p -and $p.Name -like "python*" -and -not ($procs | Where-Object { $_.ProcessId -eq $p.ProcessId })) { $procs += $p }
}
if (-not $procs) { Write-Host "AIRTAC Advance isn't running."; exit 0 }
$procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
for ($i = 0; $i -lt 20 -and (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Milliseconds 250 }
Write-Host "Stopped AIRTAC Advance."
