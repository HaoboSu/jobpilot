@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting JobPilot ...
python -m app.server
if errorlevel 1 (
  echo.
  echo Python not found or failed to start. Please install Python 3.9+ and retry.
  pause
)
