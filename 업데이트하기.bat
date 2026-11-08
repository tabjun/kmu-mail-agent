@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Mail Agent update is starting...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update_windows.ps1" %*
echo.
pause
