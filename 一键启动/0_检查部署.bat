@echo off
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\1011_部署检查_v1.ps1" -ClientRequired
pause
