@echo off
rem FlowMask one-click demo wrapper (ASCII only on purpose).
rem Usage: scripts\demo.cmd [BaseUrl] [-NoBrowser]
setlocal
set "BASE=%~1"
if "%BASE%"=="" set "BASE=http://127.0.0.1:8090"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0demo.ps1" -BaseUrl "%BASE%" %2 %3
endlocal