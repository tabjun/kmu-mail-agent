@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Mail Agent setup is starting...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_windows.ps1" %*
echo.
pause
