param([switch]$IncludeCapacity,[ValidateRange(10,3600)][int]$CapacitySeconds=120)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path $PSScriptRoot -Parent
$deployment=& (Join-Path $PSScriptRoot '1011_部署检查_v1.ps1') -AsObject
$folder=Join-Path $projectRoot ('memory/1011_验收执行_v1/'+(Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Path $folder -Force | Out-Null
$tests=@('1010_经济验收_v1.py','1010_成长验收_v1.py','1010_房间验收_v1.py','1010_帧调度验收_v1.py','1010_原生战斗协议验收_v1.py','1010_双端TCP战斗验收_v1.py','1011_可靠性验收_v1.py','1011_模块边界验收_v1.py','1011_大厅拆分验收_v1.py','1011_数据层等价验收_v1.py','1011_登录补发等价验收_v1.py','1011_功能数据层等价验收_v1.py','1011_好友数据层等价验收_v1.py','1011_通行证数据层等价验收_v1.py','1011_经济数据层等价验收_v1.py','1011_禁选意向验收_v1.py')
if($IncludeCapacity) {$tests+='1011_百人承载验收_v1.py'}
$results=@()
$originalEncoding=$env:PYTHONIOENCODING
try {
    $env:PYTHONIOENCODING='utf-8'
    foreach($name in $tests) {
        $arguments=@((Join-Path $PSScriptRoot ('1010_本地私服_v1/'+$name)))
        if($name -eq '1011_百人承载验收_v1.py') {$arguments+=@('--seconds',$CapacitySeconds,'--input-hz',30)}
        $log=Join-Path $folder ($name+'.log')
        & $deployment.pythonPath @arguments > $log 2>&1
        $code=$LASTEXITCODE
        $results+=@{test=$name;passed=($code -eq 0);exitCode=$code;log=$log}
        $results | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $folder '1011_结果_v1.json') -Encoding UTF8
        if($code -ne 0) {throw "验收失败：$name，查看 $log；独立测试库和证据已保留"}
        Write-Output "通过：$name"
    }
    Write-Output "验收完成，记录：$folder。真实跨电脑游戏对局仍待验。"
} finally {$env:PYTHONIOENCODING=$originalEncoding}
