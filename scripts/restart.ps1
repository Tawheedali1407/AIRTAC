# Restarts AIRTAC Advance's server so new code loads (used by restart_airtac.bat and Settings > Restart AIRTAC).
#   -Delay 2      wait first (the Restart button gives the page time to get its answer)
#   -NoWindow     don't open a new AIRTAC window (the page that pressed Restart reconnects by itself)
param([int]$Delay = 0, [switch]$NoWindow)
if ($Delay -gt 0) { Start-Sleep -Seconds $Delay }
& "$PSScriptRoot\stop.ps1"
if ($NoWindow) { & "$PSScriptRoot\start.ps1" -ServerOnly } else { & "$PSScriptRoot\start.ps1" }
