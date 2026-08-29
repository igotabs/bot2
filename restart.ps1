# Robust single-instance bot restart.
#
# Two guarantees against ever running two competing bot processes:
#   1. A machine-wide mutex serializes restarts, so two restart.ps1 runs (e.g.
#      autorun + manual double-click) cannot race each other.
#   2. All existing bot processes are killed and we WAIT until they are really
#      gone before starting a new one. The bot itself also holds an OS lock
#      (utils/single_instance.py), so even if something slips through, the
#      second process exits immediately instead of polling.
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads .ps1 without a
# BOM using the system code page, which corrupts non-ASCII text and breaks the
# parser. User-facing Ukrainian strings live in the bot, not here. test

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

function Get-BotProcs {
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*main.py*' }
}

# --- 1. Serialize restarts machine-wide ------------------------------------- #
$mutex = New-Object System.Threading.Mutex($false, 'Global\bot2_restart_mutex')
if (-not $mutex.WaitOne(0)) {
    Write-Output 'Another restart is already in progress - exiting.'
    exit 0
}

try {
    # --- 2. Stop every existing instance and wait for them to exit ---------- #
    foreach ($p in Get-BotProcs) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-BotProcs) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 300 }
    if (Get-BotProcs) {
        Write-Error 'Failed to stop old bot processes.'
        exit 1
    }

    # --- 3. Start exactly one new instance ---------------------------------- #
    if (-not (Test-Path '.\.venv\Scripts\pythonw.exe')) { Write-Error 'Python not found in .venv'; exit 1 }
    if (-not (Test-Path '.\main.py')) { Write-Error 'main.py not found'; exit 1 }

    $bot = Start-Process -FilePath '.\.venv\Scripts\pythonw.exe' `
        -ArgumentList @('-u', 'main.py') `
        -WorkingDirectory $PSScriptRoot `
        -RedirectStandardOutput '.\bot.out.log' `
        -RedirectStandardError '.\bot.err.log' `
        -WindowStyle Hidden -PassThru
    Set-Content -Path '.\.botpid' -Value $bot.Id -Encoding Ascii

    # --- 4. Verify it stayed up (did not exit on the single-instance lock) -- #
    Start-Sleep -Seconds 4
    if (-not (Get-Process -Id $bot.Id -ErrorAction SilentlyContinue)) {
        Write-Error 'Bot did not start or exited immediately - see bot.err.log'
        exit 1
    }

    $count = (Get-BotProcs | Measure-Object).Count
    Write-Output "Bot started (PID $($bot.Id)). Active main.py processes: $count."
}
finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
