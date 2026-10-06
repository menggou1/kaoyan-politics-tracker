@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tracker.ps1"
if errorlevel 1 (
  echo.
  echo Startup failed. Review the message above or logs folder.
  pause
)
