param([string]$PythonPath='', [string]$ServerIp='127.0.0.1', [switch]$ClientRequired, [switch]$AsObject)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot '1011_网络规则_v1.ps1')
if(!(Test-ReaNetworkAddress $ServerIp)) {throw "只允许本机、局域网、Radmin VPN、Tailscale IPv4 地址：$ServerIp"}
$configPath=Join-Path $projectRoot 'drafts/1011_运行配置_v1.json'
if(!$PythonPath -and (Test-Path -LiteralPath $configPath)) {
    $config=Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $PythonPath=$config.pythonPath
}
if(!$PythonPath) {$PythonPath=$env:REA_PYTHON}
if(!$PythonPath) {
    $candidates=@((Join-Path $projectRoot 'tools/python/python.exe'), (Join-Path $projectRoot '.venv/Scripts/python.exe'))
    $command=Get-Command python.exe -ErrorAction SilentlyContinue
    if($command -and $command.Source -notmatch '[/\\]WindowsApps[/\\]') {$candidates+=$command.Source}
    $candidates+=(Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe')
    $PythonPath=$candidates | Where-Object {Test-Path -LiteralPath $_ -PathType Leaf} | Select-Object -First 1
}
if(!$PythonPath -or !(Test-Path -LiteralPath $PythonPath -PathType Leaf)) {throw '未找到 Python：安装 Python 3.12，再设置 REA_PYTHON 或 drafts/1011_运行配置_v1.json 的 pythonPath'}
$PythonPath=[IO.Path]::GetFullPath($PythonPath)
$required=@('content/1010_微端完整数据_v1.json','content/1010_原生资源清单_v1.json','scripts/1010_本地私服_v1/1010_server_v1.py','tools/python-libs/google/protobuf/__init__.py')
if($ClientRequired) {$required+=@('assets/1010_本地客户端_v1/hdjt.exe','assets/1010_本地客户端_v1/hdjt_Data/Managed/Assembly-CSharp.dll','tools/mono-cecil-0.11.6/package/lib/netstandard2.0/Mono.Cecil.dll')}
foreach($relative in $required) {if(!(Test-Path -LiteralPath (Join-Path $projectRoot $relative) -PathType Leaf)) {throw "缺少部署文件：$relative。请恢复完整备份并覆盖 main 最新代码"}}
$deployScript=Join-Path $projectRoot 'scripts/1010_本地私服_v1/1011_deploy_check_v1.py'
$checkArguments=@($deployScript,'--root',$projectRoot)
if($ClientRequired) {$checkArguments+='--client'}
$runtimeOutput=& $PythonPath @checkArguments
if($LASTEXITCODE -ne 0) {throw 'Python、协议或部署文件检查失败，请查看上方错误'}
$runtimeInfo=($runtimeOutput -join '') | ConvertFrom-Json
$result=[pscustomobject]@{pythonPath=$PythonPath;pythonVersion=$runtimeInfo.python_version;buildId=$runtimeInfo.build_id;serverIp=$ServerIp;projectRoot=$projectRoot}
if($AsObject) {$result} else {Write-Output "部署检查通过：Python $($result.pythonVersion)，地址 $ServerIp。端口和实际联机仍需验证。"}
