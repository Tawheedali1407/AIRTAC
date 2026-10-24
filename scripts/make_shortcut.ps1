# Puts "AIRTAC Advance" on your desktop and in the Start menu. -AtLogin also starts AIRTAC's server,
# hidden and without a window, when you sign in to Windows, so the app never opens to a dead server.
# To undo -AtLogin: Win+R, type shell:startup, delete "AIRTAC Advance (server).lnk".
param([switch]$AtLogin)
$root = Split-Path -Parent $PSScriptRoot
$shell = New-Object -ComObject WScript.Shell
$folders = @([Environment]::GetFolderPath("Desktop"), (Join-Path ([Environment]::GetFolderPath("Programs")) ""))
if ($AtLogin) {
    $folders += [Environment]::GetFolderPath("Startup")
    # An older version put the window-opening shortcut in Startup; the server-only one replaces it.
    Remove-Item (Join-Path ([Environment]::GetFolderPath("Startup")) "AIRTAC Advance.lnk") -ErrorAction SilentlyContinue
}
foreach ($folder in $folders) {
    $atStart = $folder -eq [Environment]::GetFolderPath("Startup")
    $name = if ($atStart) { "AIRTAC Advance (server).lnk" } else { "AIRTAC Advance.lnk" }
    $link = $shell.CreateShortcut((Join-Path $folder $name))
    $link.TargetPath = "powershell.exe"
    $only = if ($atStart) { " -ServerOnly" } else { "" }
    $link.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\scripts\start.ps1`"$only"
    $link.WorkingDirectory = $root
    $link.IconLocation = "$root\web\icons\airtac-wheel.ico"
    $link.Description = "Open AIRTAC Advance"
    $link.Save()
    Write-Host "Made 'AIRTAC Advance' in $folder"
}
# Windows keeps old shortcut icons in a cache. A new icon file name plus this refresh
# makes the new AIRTAC wheel show straight away (sign out and in if it still doesn't).
try { & "$env:SystemRoot\System32\ie4uinit.exe" -show } catch { }
