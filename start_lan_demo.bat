@echo off
rem Same as start_gpt_demo.bat, but other PCs on the same Wi-Fi / office network can open it too.
rem Web: http://<this PC IP>:8000   MCP: http://<this PC IP>:8765/mcp/<secret>/<slug>  (see the AI connect menu)
rem Windows may ask to allow Python through the firewall: allow it on "Private network".
chcp 65001 >nul
cd /d "%~dp0"
set "APP=.venv\Scripts\approval-server.exe"
if not exist "%APP%" (
  echo [setup] .venv not found. Running: uv sync
  uv sync || goto :fail
)
echo [1/2] Resetting demo data (seed, demo clock 2026-11-12 10:00) ...
"%APP%" reset || goto :fail
echo [2/2] Starting approval server + MCP for the local network ...
"%APP%" gpt --lan %*
pause
exit /b 0
:fail
echo.
echo [ERROR] Setup failed. See the message above.
pause
exit /b 1
