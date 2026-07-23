@echo off
REM Room suite installer entry (Windows). Forwards to PowerShell script.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
if errorlevel 1 (
  echo.
  echo Install failed. If script is blocked, run:
  echo   powershell -ExecutionPolicy Bypass -File install.ps1
  exit /b 1
)
endlocal
