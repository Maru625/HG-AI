@echo off
rem Lead PC: start the server, then place the site (left 2/3) and ChatGPT (right 1/3). Other user: start_lead_pc.bat -User E2003
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0demo_layout.ps1" -Role lead %*
