@echo off
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\1011_停止本项目服务_v1.ps1"
pause
