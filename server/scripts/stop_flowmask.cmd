@echo off
rem Stop the running FlowMask instance started from FlowMask.exe (desktop shortcut target).
rem Keep this file ASCII-only: classic cmd.exe decodes batch files with the OEM codepage.
setlocal
set "HERE=%~dp0"
set "EXE=%HERE%..\dist_exe\FlowMask.exe"

if not exist "%EXE%" goto fallback

"%EXE%" --stop
if errorlevel 1 (
  echo.
  echo FlowMask: no running instance found.
) else (
  echo.
  echo FlowMask: stopped.
)
echo Log: %%LOCALAPPDATA%%\FlowMask\run\last-stop.txt
rem ping instead of timeout: timeout needs a real console stdin and fails when piped.
ping -n 4 127.0.0.1 >nul
exit /b 0

:fallback
echo FlowMask.exe not found, falling back to stop_local.ps1 ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%stop_local.ps1"
ping -n 4 127.0.0.1 >nul
exit /b 0
