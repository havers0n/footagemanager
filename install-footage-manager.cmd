@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
echo Installing Footage Manager core...
wsl.exe bash -lc "cd '$(wslpath '%SCRIPT_DIR%')' && chmod +x scripts/*.sh && ./scripts/setup-wsl.sh"
if errorlevel 1 (
  echo Installation did not complete. Read the message above.
  pause
  exit /b 1
)
echo Installation complete.
pause

