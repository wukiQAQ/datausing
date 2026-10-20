@echo off
rem FlowMask 一键启动（Windows 双击/命令行均可）
rem
rem 说明：Windows PowerShell 默认执行策略可能禁止直接运行 .ps1，
rem 这里显式用 -ExecutionPolicy Bypass 调起，参数原样透传。
rem
rem 例子：
rem   scripts\start_local.cmd
rem   scripts\start_local.cmd -WithL2 -EnableNer
rem   scripts\start_local.cmd -WithL2 -WithL3 -L3Device cuda -NoBrowser
rem   scripts\start_local.cmd -Port 8095
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_local.ps1" %*
endlocal
