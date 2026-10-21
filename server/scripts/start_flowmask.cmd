@echo off
rem Start FlowMask from the desktop shortcut (detach so the console window closes immediately).
rem Keep this file ASCII-only: classic cmd.exe decodes batch files with the OEM codepage.
setlocal
set "HERE=%~dp0"
set "EXE=%HERE%..\dist_exe\FlowMask.exe"

if not exist "%EXE%" (
  echo FlowMask.exe not found: %EXE%
  ping -n 4 127.0.0.1 >nul
  exit /b 1
)

rem Working directory must be the exe folder: the onefile bundle unpacks to .flowmask-tmp
rem relative to the current directory, and portable data/logs also live next to the exe.
cd /d "%HERE%..\dist_exe"
start "" "%EXE%" %*
exit /b 0
