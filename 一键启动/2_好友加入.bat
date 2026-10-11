@echo off
cd /d "%~dp0.."
set /p SIP=输入房主局域网IP:
set /p ACC=输入你的账号名(字母数字下划线):
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\1010_好友加入_v1.ps1" -ServerIp %SIP% -Account %ACC%
pause
