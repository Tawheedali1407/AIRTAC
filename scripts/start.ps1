# Opens AIRTAC Advance like an app: starts the server in the background if it isn't running, waits
# until /api/health answers, then opens its own window (allowed to speak, so it reads out your day).
#   -ServerOnly   start the server but open no window (used by the "Start AIRTAC" button, airtac://start)
#   -Port 8765    another port than the one in config/airtac.toml
param([int]$Port = 0, [switch]$ServerOnly)
$root = Split-Path -Parent $PSScriptRoot
if ($Port -eq 0) {
    $Port = 8765
    $line = Select-String -Path "$root\config\airtac.toml" -Pattern '^\s*port\s*=\s*(\d+)' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($line) { $Port = [int]$line.Matches[0].Groups[1].Value }
}
$url = "http://localhost:$Port"
$log = "$root\data\logs\server.log"

function Say($text) { Write-Host $text }
# Launched from the hidden shortcut nobody sees the console, so a failure also gets a message box.
function Fail($text) {
    Write-Host $text -ForegroundColor Red
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show($text, "AIRTAC Advance", "OK", "Warning") | Out-Null
    } catch { }
    exit 1
}
function Test-Airtac { try { return (Invoke-RestMethod "$url/api/health" -TimeoutSec 3).status -eq "ok" } catch { return $false } }
function Get-PortOwner {
    $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { Get-CimInstance Win32_Process -Filter "ProcessId = $($c.OwningProcess)" }
}
function Test-IsAirtac($proc) { $proc.CommandLine -like "*airtac.main*" -or $proc.CommandLine -like "*scripts\serve.py*" }

# The "Start AIRTAC" button on the page opens airtac://start. Windows needs to know that link runs this
# script: registered for this Windows user only (no admin), refreshed each start so a moved folder still
# works. The link passes nothing through (no %1), so a web page can only ever start AIRTAC, nothing else.
# To remove it:  reg delete HKCU\Software\Classes\airtac /f
try {
    $key = "HKCU:\Software\Classes\airtac"
    $cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`" -ServerOnly"
    if ((Get-ItemProperty "$key\shell\open\command" -ErrorAction SilentlyContinue).'(default)' -ne $cmd) {
        New-Item -Path "$key\shell\open\command" -Force | Out-Null
        Set-ItemProperty -Path $key -Name "(default)" -Value "URL:AIRTAC start"
        Set-ItemProperty -Path $key -Name "URL Protocol" -Value ""
        Set-ItemProperty -Path "$key\shell\open\command" -Name "(default)" -Value $cmd
    }
} catch { Say "Couldn't register the airtac://start link (the Start button on the page won't work): $_" }

# One launcher at a time (fix, 24 Oct). AIRTAC takes about 20 s to load and the hidden launcher shows nothing
# meanwhile, so a second double-click (or the sign-in shortcut, or airtac://start) used to start a second and
# third server and, about 20 s later, open one window per click: AIRTAC "opened by itself". The log showed it:
# 2-3 "starting AIRTAC" lines a few seconds apart, then "port 8765 is in use". Now a second launcher waits for
# the first one and doesn't open another window if the first one is about to open it.
$mutex = New-Object System.Threading.Mutex($false, "Local\AIRTAC-Advance-launcher")
$owned = $false
try { $owned = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $owned = $true }
if (-not $owned) {
    Say "AIRTAC is already starting (another launcher is on it). Waiting for it..."
    $got = $false
    try { $got = $mutex.WaitOne(90000) } catch [System.Threading.AbandonedMutexException] { $got = $true }
    if ($got) { $mutex.ReleaseMutex() }
    $stamp = "$root\data\logs\window.opened"
    if (-not $ServerOnly -and (Test-Path $stamp) -and ((Get-Date) - (Get-Item $stamp).LastWriteTime).TotalSeconds -lt 15) {
        Say "The other launcher just opened the window."
        exit 0
    }
    $owned = $mutex.WaitOne(0)
}
# A server that is still loading (process alive, port not open yet) counts as running: wait, don't start another.
function Get-LoadingAirtac {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { Test-IsAirtac $_ } | Select-Object -First 1
}

if (Test-Airtac) {
    Say "AIRTAC is already running at $url"
} elseif ($loading = Get-LoadingAirtac) {
    Say "AIRTAC (process $($loading.ProcessId)) is still loading. Waiting for it..."
    for ($i = 0; $i -lt 120 -and -not (Test-Airtac); $i++) { Start-Sleep -Milliseconds 500 }
    if (-not (Test-Airtac)) { Fail "AIRTAC (process $($loading.ProcessId)) is running but doesn't answer. Run scripts\stop.ps1, then start it again." }
} else {
    $python = "$root\.venv\Scripts\python.exe"
    if (-not (Test-Path $python)) { Fail "AIRTAC isn't installed yet. Double-click Install-AIRTAC.cmd first." }

    # Something already listening on the port but not answering: an old AIRTAC that hung or is still
    # shutting down is stopped; anything else is left alone and named, so you can decide.
    $owner = Get-PortOwner
    if ($owner) {
        if (Test-IsAirtac $owner) {
            Say "An old AIRTAC (process $($owner.ProcessId)) holds port $Port but doesn't answer. Stopping it."
            Stop-Process -Id $owner.ProcessId -Force -ErrorAction SilentlyContinue
            for ($i = 0; $i -lt 20 -and (Get-PortOwner); $i++) { Start-Sleep -Milliseconds 250 }
        } else {
            Fail "Port $Port is used by another program: $($owner.Name) (process $($owner.ProcessId)). Close it, or set another port under [server] in config\airtac.toml."
        }
    }

    # Make sure Ollama is up (it usually starts with Windows).
    if ((Get-Command ollama -ErrorAction SilentlyContinue) -and -not (Get-Process ollama -ErrorAction SilentlyContinue)) {
        Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    }
    Say "Starting AIRTAC on port $Port..."
    $server = Start-Process -FilePath $python -ArgumentList "`"$root\scripts\serve.py`"", "--port", $Port `
        -WorkingDirectory $root -WindowStyle Hidden -PassThru
    for ($i = 0; $i -lt 120 -and -not (Test-Airtac); $i++) {
        if ($server.HasExited) { break }
        Start-Sleep -Milliseconds 500
    }
    if (-not (Test-Airtac)) {
        $tail = if (Test-Path $log) { (Get-Content $log -Tail 6) -join "`n" } else { "(no log yet)" }
        Fail "AIRTAC didn't start. The last lines of $log :`n`n$tail"
    }
    Say "AIRTAC is running at $url (process $($server.Id)). Log: $log"
}

if ($ServerOnly) { if ($owned) { $mutex.ReleaseMutex() }; exit 0 }

# Remember when a window was opened, so a launcher that was waiting on this one doesn't open a second one.
New-Item -ItemType Directory -Force -Path "$root\data\logs" | Out-Null
Set-Content -Path "$root\data\logs\window.opened" -Value (Get-Date -Format o)

$browserData = Join-Path $env:LOCALAPPDATA "AIRTAC-Advance\browser"
# --hide-crash-restore-bubble / --disable-session-crashed-bubble: when Windows shut down with AIRTAC open, Edge
# offered to "restore" the AIRTAC window on its next start, which also looked like AIRTAC opening by itself.
$browserArgs = @("--app=$url", "--user-data-dir=`"$browserData`"", "--autoplay-policy=no-user-gesture-required",
                 "--hide-crash-restore-bubble", "--disable-session-crashed-bubble", "--no-first-run")
# Find browsers via App Paths registry (they are often not on PATH).
$edge = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe" -ErrorAction SilentlyContinue).'(Default)'
if (-not $edge) { $edge = "msedge" }
$chrome = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" -ErrorAction SilentlyContinue).'(Default)'
if (-not $chrome) { $chrome = "chrome" }
try { Start-Process $edge -ArgumentList $browserArgs -ErrorAction Stop }
catch { try { Start-Process $chrome -ArgumentList $browserArgs -ErrorAction Stop } catch { Start-Process $url } }
if ($owned) { $mutex.ReleaseMutex() }
