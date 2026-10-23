@echo off
rem ChatGPT demo launcher: approval server + MCP (HTTP, 2 connections on 127.0.0.1:8765)
rem ChatGPT desktop app connects to these local addresses directly. No public tunnel.
rem Double-click this file. Keep the window open during the demo. Close it (or Ctrl+C) to stop.
chcp 65001 >nul
cd /d "%~dp0"
set "APP=.venv\Scripts\approval-server.exe"
if not exist "%APP%" (
  echo [setup] .venv not found. Running: uv sync
  uv sync || goto :fail
)
echo [1/2] Resetting demo data (seed, demo clock 2026-11-12 10:00) ...
"%APP%" reset || goto :fail
echo [2/2] Starting approval server + MCP ...
"%APP%" gpt %*
pause
exit /b 0
:fail
echo.
echo [ERROR] Setup failed. See the message above.
pause
exit /b 1
