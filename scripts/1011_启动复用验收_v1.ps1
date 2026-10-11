param([Parameter(Mandatory)][string]$PythonPath)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path $PSScriptRoot -Parent
$fixture=Join-Path $projectRoot ('drafts/1011_换目录启动验收_v1/'+[guid]::NewGuid().ToString('N'))
foreach($port in @(28798,28799,28800)) {
    if(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {throw "端口 $port 已有人使用，拒绝启动验收，不影响现有服务"}
}
foreach($relative in @('scripts/1010_本地私服_v1','tools/python-libs/google','content','drafts')) {New-Item -ItemType Directory -Path (Join-Path $fixture $relative) -Force | Out-Null}
Copy-Item -Path (Join-Path $projectRoot 'scripts/1010_本地私服_v1/*.py') -Destination (Join-Path $fixture 'scripts/1010_本地私服_v1')
foreach($name in @('1010_启动本地私服_v1.ps1','1011_部署检查_v1.ps1','1011_网络规则_v1.ps1','1011_网络规则_v1.json')) {Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $fixture 'scripts')}
foreach($name in @('1010_微端完整数据_v1.json','1010_原生资源清单_v1.json')) {Copy-Item -LiteralPath (Join-Path $projectRoot ('content/'+$name)) -Destination (Join-Path $fixture 'content')}
Copy-Item -LiteralPath (Join-Path $projectRoot 'tools/python-libs/google/protobuf') -Destination (Join-Path $fixture 'tools/python-libs/google') -Recurse
Copy-Item -LiteralPath (Join-Path $projectRoot 'tools/python-libs/google/_upb') -Destination (Join-Path $fixture 'tools/python-libs/google') -Recurse
@{pythonPath=[IO.Path]::GetFullPath($PythonPath)} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $fixture 'drafts/1011_运行配置_v1.json') -Encoding UTF8
$serverPath=Join-Path $fixture 'scripts/1010_本地私服_v1/1010_server_v1.py'
$ids=@()
try {
    foreach($runNumber in 1..2) {
        & (Join-Path $fixture 'scripts/1010_启动本地私服_v1.ps1') -ServerOnly -GamePort 28798 -HttpPort 28799 -BattlePort 28800
        $processes=@(Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -like ('*'+$serverPath+'*') -and $_.ExecutablePath -eq [IO.Path]::GetFullPath($PythonPath)})
        if($processes.Count -ne 1) {throw "第 $runNumber 次启动的服务数量不是 1"}
        $ids+=$processes[0].ProcessId
        $listeners=@(Get-NetTCPConnection -State Listen -LocalPort 28798,28799,28800 | Select-Object LocalPort,OwningProcess -Unique)
        if($listeners.Count -ne 3 -or @($listeners | Where-Object {$_.OwningProcess -ne $processes[0].ProcessId}).Count) {throw '端口归属或数量异常'}
    }
    if($ids[0] -ne $ids[1]) {throw '第二次启动没有复用原进程'}
    $report=@{passed=@('换到全新项目目录独立存档开服','连续启动两次服务进程保持一份','三个端口均归属同一个进程');processIds=$ids;fixture=$fixture;clientWindowVerified=$false;browserTabsOpened=0}
    $report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $projectRoot 'content/1011_启动复用验收_v1.json') -Encoding UTF8
    Write-Output '换目录与服务复用验收通过；未打开游戏窗口或浏览器。'
} finally {
    Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -like ('*'+$serverPath+'*') -and $_.ExecutablePath -eq [IO.Path]::GetFullPath($PythonPath)} | ForEach-Object {Stop-Process -Id $_.ProcessId}
}
