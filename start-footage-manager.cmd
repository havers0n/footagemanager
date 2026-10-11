@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
start "Footage Manager" /min wsl.exe bash -lc "cd '$(wslpath '%SCRIPT_DIR%')' && ./scripts/start.sh > /tmp/footage-manager.log 2>&1"
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:8787
echo Footage Manager started. WSL log: /tmp/footage-manager.log
