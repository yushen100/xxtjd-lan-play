@echo off
cd /d "%~dp0.."
set /p ACC=输入你的账号名(字母数字下划线):
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\1010_局域网开服_v1.ps1" -Account %ACC%
pause
