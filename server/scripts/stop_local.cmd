@echo off
rem FlowMask 停止本机所有由 start_local 启动的进程（Windows）
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_local.ps1" %*
endlocal
