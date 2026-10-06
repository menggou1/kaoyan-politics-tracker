@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tracker.ps1" -Stop
if errorlevel 1 pause
