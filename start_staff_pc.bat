@echo off
rem Staff PC: connect to the lead PC server, then place the site (left 2/3) and ChatGPT (right 1/3). Needs only this file and demo_layout.ps1
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0demo_layout.ps1" -Role staff %*
