param([ValidatePattern('^[A-Za-z0-9_]{2,16}$')][string]$Account='10001',[switch]$ServerOnly,[string]$HostIp='127.0.0.1',[ValidateRange(1024,65535)][int]$GamePort=18798,[ValidateRange(1024,65535)][int]$HttpPort=18799,[ValidateRange(1024,65535)][int]$BattlePort=18800)
$ErrorActionPreference='Stop'
if(@($GamePort,$HttpPort,$BattlePort | Select-Object -Unique).Count -ne 3) {throw '三个端口必须不同'}
if(!$ServerOnly -and ($GamePort -ne 18798 -or $HttpPort -ne 18799 -or $BattlePort -ne 18800)) {throw '自定义端口仅用于 ServerOnly，客户端入口使用默认端口'}
$startupMutex=[Threading.Mutex]::new($false,'Local\REA1010LocalServerStartup')
$mutexOwned=$false
try {
    try { $mutexOwned=$startupMutex.WaitOne(15000) } catch [Threading.AbandonedMutexException] {$mutexOwned=$true}
    if(!$mutexOwned) {throw '另一个启动操作仍在运行，请查看服务日志'}
$projectRoot=Split-Path $PSScriptRoot -Parent
$deployment=& (Join-Path $PSScriptRoot '1011_部署检查_v1.ps1') -ServerIp $HostIp -ClientRequired:(!$ServerOnly) -AsObject
$pythonPath=$deployment.pythonPath
$expectedBuild=$deployment.buildId
$serverPath=Join-Path $projectRoot 'scripts/1010_本地私服_v1/1010_server_v1.py'
$clientRoot=Join-Path $projectRoot 'assets/1010_本地客户端_v1'
$logRoot=Join-Path $projectRoot 'memory/1010_本地私服日志_v1'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
# 根据完整项目路径核验归属，不停止占用端口的其他程序。
foreach($port in @($GamePort,$HttpPort,$BattlePort)) {
    $listeners=@(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
    foreach($listener in $listeners) {
        $processInfo=Get-CimInstance Win32_Process -Filter ('ProcessId='+$listener.OwningProcess)
        if($processInfo.CommandLine -notlike ('*'+$serverPath+'*')) {throw "端口 $port 被其他程序占用，停止启动"}
    }
}
$running=@(Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -like ('*'+$serverPath+'*')})
if($running.Count -gt 1) {throw '发现本项目有多个服务进程，请核对后保留一份'}
if($running.Count -eq 0) {
    Start-Process -FilePath $pythonPath -ArgumentList ('"'+$serverPath+'" --game-port '+$GamePort+' --http-port '+$HttpPort+' --battle-port '+$BattlePort+$(if($HostIp -ne '127.0.0.1'){' --lan --host-ip '+$HostIp}else{''})) -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot '1010_服务输出_v1.log') -RedirectStandardError (Join-Path $logRoot '1010_服务错误_v1.log') | Out-Null
}
$ready=$false
foreach($attempt in 1..20) {
    try { $health=Invoke-RestMethod "http://127.0.0.1:${HttpPort}/health" -TimeoutSec 2; if($health.service -eq 'native-local-v1') {$ready=$true;break} } catch {}
    Start-Sleep -Milliseconds 250
}
if(!$ready) { throw ('服务未启动，请检查 '+$logRoot) }
if($health.build_id -ne $expectedBuild) {throw '正在运行的服务与当前代码版本不同，请先关闭本项目服务再启动；不会自动中断现有对局'}
if(!$ServerOnly) {
    $clientPath=[IO.Path]::GetFullPath((Join-Path $clientRoot 'hdjt.exe'))
    if($Account -eq '10002') {
        # 不同程序名让窗口控制工具能够准确区分本地A/B，资源仍共用项目副本。
        $secondClientPath=Join-Path $clientRoot 'hdjtB.exe'
        if(!(Test-Path -LiteralPath $secondClientPath)) {Copy-Item -LiteralPath $clientPath -Destination $secondClientPath}
        $secondDataPath=Join-Path $clientRoot 'hdjtB_Data'
        $sharedDataPath=[IO.Path]::GetFullPath((Join-Path $clientRoot 'hdjt_Data'))
        if(!(Test-Path -LiteralPath $secondDataPath)) {New-Item -ItemType Junction -Path $secondDataPath -Target $sharedDataPath | Out-Null}
        if((Get-Item -LiteralPath $secondDataPath).Target -ne $sharedDataPath) {throw '角色B数据目录目标不匹配，停止启动'}
        $clientPath=[IO.Path]::GetFullPath($secondClientPath)
    }
    $clients=@(Get-CimInstance Win32_Process | Where-Object {$_.ExecutablePath -eq $clientPath -and $_.CommandLine -match ('\s'+$Account+'(?:\s|$)')})
    if($clients.Count -eq 0) {
        $proc = Start-Process -FilePath $clientPath -ArgumentList $Account -WorkingDirectory $clientRoot -PassThru
        # 后台把窗口改成可拖动调整大小
        Start-Process powershell -WindowStyle Hidden -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $PSScriptRoot '1011_窗口可调整大小_v1.ps1'),'-ProcessId',$proc.Id) | Out-Null
    }
}
Write-Output "本地服务已运行；账号 $Account。大厅和战斗验收待完成。"
} finally {
    if($mutexOwned) {$startupMutex.ReleaseMutex()}
    $startupMutex.Dispose()
}
