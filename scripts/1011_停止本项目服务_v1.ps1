$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$serverPath=Join-Path $root 'scripts/1010_本地私服_v1/1010_server_v1.py'
$processes=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -in @('python.exe','pythonw.exe') -and $_.CommandLine -like ('*'+$serverPath+'*')})
foreach($processInfo in $processes) {Stop-Process -Id $processInfo.ProcessId}
Write-Output "已停止本项目 $($processes.Count) 个服务进程。其他目录服务和游戏窗口未处理。"
