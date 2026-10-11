# 房主端：自动取本机局域网IPv4，项目客户端补丁指向该IP，服务监听局域网（只接受私网来源）。
param([ValidatePattern('^[A-Za-z0-9_]{2,16}$')][string]$Account='10001',[string]$HostIp='')
$ErrorActionPreference='Stop'
if(!$HostIp) {
    $HostIp=(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -match '^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)' -and $_.PrefixOrigin -ne 'WellKnown' } |
        Sort-Object InterfaceMetric | Select-Object -First 1).IPAddress
    if(!$HostIp) {throw '未找到局域网IPv4，请用 -HostIp 指定'}
}
Write-Output "房主局域网IP：$HostIp （告诉好友这个地址）"
if(!(Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -eq $HostIp})) {throw '房主 IP 不是本机网卡地址，请检查局域网 / VPN 网卡'}
# 地址或补丁改变前只处理本项目的服务，并由启动入口复用服务。
& (Join-Path $PSScriptRoot '1011_部署检查_v1.ps1') -ServerIp $HostIp -ClientRequired
$serverPath=Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts/1010_本地私服_v1/1010_server_v1.py'
$running=@(Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -like ('*'+$serverPath+'*')})
foreach($processInfo in $running) {
    $sameHost=$processInfo.CommandLine -match ('--host-ip\s+'+[regex]::Escape($HostIp)+'(?:\s|$)')
    if(!$sameHost) {throw '本项目已用其他地址开服。请先关闭本项目服务，再按新地址开服，避免中断现有对局。'}
}
. (Join-Path $PSScriptRoot '1011_客户端检查_v1.ps1')
$projectRoot=Split-Path $PSScriptRoot -Parent
$clientRoot=Join-Path $projectRoot 'assets/1010_本地客户端_v1'
$patchScript=Join-Path $PSScriptRoot '1010_切换本地启动_v1.ps1'
$validPatch=Test-ReaClientPatch -ClientRoot $clientRoot -RecordPath (Join-Path $projectRoot 'content/1010_本地启动补丁_v1.json') -ServerIp $HostIp -PatchScript $patchScript
$clients=@(Get-CimInstance Win32_Process | Where-Object {$_.ExecutablePath -in @((Join-Path $clientRoot 'hdjt.exe'),(Join-Path $clientRoot 'hdjtB.exe'))})
if(!$validPatch -and $clients.Count) {throw '补丁或服务器地址有变化，请先关闭本项目游戏窗口再启动'}
if(!$validPatch) {& $patchScript -ServerIp $HostIp}
& (Join-Path $PSScriptRoot '1010_启动本地私服_v1.ps1') -Account $Account -HostIp $HostIp
