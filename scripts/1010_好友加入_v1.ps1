# 好友端：从本机自己安装的官方客户端复制一份到本工具旁边，打补丁指向房主局域网IP后启动。不修改官方安装目录。
param([Parameter(Mandatory)][string]$ServerIp,[Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9_]{2,16}$')][string]$Account)
$ErrorActionPreference='Stop'
# 在复制和打补丁之前验证地址，避免错误输入产生半成品。
. (Join-Path $PSScriptRoot '1011_网络规则_v1.ps1')
if(!(Test-ReaNetworkAddress $ServerIp)) {throw "只允许本机、局域网、Radmin VPN、Tailscale IPv4 地址：$ServerIp"}
$startupMutex=[Threading.Mutex]::new($false,'Local\REAFriendClientStartup')
$mutexOwned=$false
try {
    try {$mutexOwned=$startupMutex.WaitOne(15000)} catch [Threading.AbandonedMutexException] {$mutexOwned=$true}
    if(!$mutexOwned) {throw '另一个好友端启动仍在进行'}
$kitRoot=Split-Path $PSScriptRoot -Parent
$bundled=Join-Path $kitRoot 'assets/1010_本地客户端_v1'
$official=if(Test-Path -LiteralPath (Join-Path $bundled 'hdjt.exe')) {$bundled} else {Join-Path $env:LOCALAPPDATA '4399/Xxtjd2'}
$clientRoot=Join-Path $kitRoot '局域网客户端'
if(!(Test-Path -LiteralPath (Join-Path $official 'hdjt.exe'))) {throw "未找到本机官方客户端：$official。请先自行安装官方微端并运行一次。"}
if(!(Test-Path -LiteralPath (Join-Path $clientRoot 'hdjt.exe'))) {
    Write-Output '首次运行：准备好友客户端副本（几分钟）...'
    Copy-Item -LiteralPath $official -Destination $clientRoot -Recurse
    # 房主用过的部署包可能已打补丁；好友副本从核验过的原件重新打补丁。
    if($official -eq $bundled) {
        $original=Join-Path $kitRoot 'drafts/1010_客户端补丁_v1/1010_Assembly-CSharp原件_v1.dll'
        if(Test-Path -LiteralPath $original) {
            if((Get-FileHash -LiteralPath $original).Hash.ToLower() -ne '5b3946c24b00131740fd69237956fa8b4d0f2eeca3fb1deb951d585287939af4') {throw '客户端原件校验失败，停止准备好友副本'}
            Copy-Item -LiteralPath $original -Destination (Join-Path $clientRoot 'hdjt_Data/Managed/Assembly-CSharp.dll')
        }
    }
}
try {
    $health=Invoke-RestMethod "http://${ServerIp}:18799/health" -TimeoutSec 3
    if($health.service -ne 'native-local-v1') {throw '服务身份不匹配'}
} catch { throw "连不上正确的房主服务 ${ServerIp}:18799，确认房主已开服且防火墙放行 18798-18800" }
$clientPath=[IO.Path]::GetFullPath((Join-Path $clientRoot 'hdjt.exe'))
$clients=@(Get-CimInstance Win32_Process | Where-Object {$_.ExecutablePath -eq $clientPath -and $_.CommandLine -match ('\s'+[regex]::Escape($Account)+'(?:\s|$)')})
. (Join-Path $PSScriptRoot '1011_客户端检查_v1.ps1')
$patchScript=Join-Path $PSScriptRoot '1010_切换本地启动_v1.ps1'
$validPatch=Test-ReaClientPatch -ClientRoot $clientRoot -RecordPath (Join-Path $clientRoot '_1010补丁备份/1010_补丁记录_v1.json') -ServerIp $ServerIp -PatchScript $patchScript
$allClients=@(Get-CimInstance Win32_Process | Where-Object {$_.ExecutablePath -eq $clientPath})
if(!$validPatch -and $allClients.Count) {throw '地址或补丁有变化，请先关闭本工具启动的好友客户端'}
if($clients.Count -gt 0) {Write-Output "账号 $Account 的好友客户端已运行，复用已有窗口";return}
if(!$validPatch) {& $patchScript -ServerIp $ServerIp -ClientDir $clientRoot}
$proc = Start-Process -FilePath (Join-Path $clientRoot 'hdjt.exe') -ArgumentList $Account -WorkingDirectory $clientRoot -PassThru
Start-Process powershell -WindowStyle Hidden -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $PSScriptRoot '1011_窗口可调整大小_v1.ps1'),'-ProcessId',$proc.Id) | Out-Null
Write-Output "已启动，账号 $Account，服务器 $ServerIp"
} finally {
    if($mutexOwned) {$startupMutex.ReleaseMutex()}
    $startupMutex.Dispose()
}
