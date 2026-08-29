@echo off
cd /d "%~dp0"

REM All restart logic lives in restart.ps1 (machine-wide mutex + stop-wait-start).
REM Bypass execution policy only for this one script so autorun works cleanly.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart.ps1"

exit /b %ERRORLEVEL%
