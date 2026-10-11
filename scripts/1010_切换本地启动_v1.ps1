param([switch]$BattleReport,[string]$ServerIp='127.0.0.1',[string]$ClientDir='')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot '1011_网络规则_v1.ps1')
if(!(Test-ReaNetworkAddress $ServerIp)) {throw "只允许本机、局域网、Radmin VPN、Tailscale IPv4 地址：$ServerIp"}
$serverBase="http://${ServerIp}:18799"
$projectRoot=Split-Path $PSScriptRoot -Parent
$clientRoot=if($ClientDir){[IO.Path]::GetFullPath($ClientDir)}else{Join-Path $projectRoot 'assets/1010_本地客户端_v1'}
$assemblyPath=Join-Path $clientRoot 'hdjt_Data/Managed/Assembly-CSharp.dll'
$backupRoot=if($ClientDir){Join-Path $clientRoot '_1010补丁备份'}else{Join-Path $projectRoot 'drafts/1010_客户端补丁_v1'}
New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
$backupPath=Join-Path $backupRoot '1010_Assembly-CSharp原件_v1.dll'
$originalHash='5b3946c24b00131740fd69237956fa8b4d0f2eeca3fb1deb951d585287939af4'
$currentHash=(Get-FileHash -LiteralPath $assemblyPath).Hash.ToLower()
$recordPath=if($ClientDir){Join-Path $backupRoot '1010_补丁记录_v1.json'}else{Join-Path $projectRoot 'content/1010_本地启动补丁_v1.json'}
if ($currentHash -ne $originalHash) {
    if (!(Test-Path $recordPath) -or ((Get-Content $recordPath -Raw | ConvertFrom-Json).patched_sha256 -ne $currentHash)) { throw '程序集与已核验原件不一致，停止修改' }
}
if (!(Test-Path $backupPath)) { Copy-Item -LiteralPath $assemblyPath -Destination $backupPath }
# 仅改变项目副本的启动参数来源，保留资源更新和版本检查。
$clientConfigPath=Join-Path $clientRoot 'config_wd.json'
if (!(Test-Path -LiteralPath $clientConfigPath)) {
    Copy-Item -LiteralPath (Join-Path $env:LOCALAPPDATA '4399/Xxtjd2/config_wd.json') -Destination $clientConfigPath
}
[Reflection.Assembly]::LoadFrom((Join-Path $projectRoot 'tools/mono-cecil-0.11.6/package/lib/netstandard2.0/Mono.Cecil.dll')) | Out-Null
$resolver=[Mono.Cecil.DefaultAssemblyResolver]::new()
$resolver.AddSearchDirectory((Join-Path $clientRoot 'hdjt_Data/Managed'))
$parameters=[Mono.Cecil.ReaderParameters]::new();$parameters.AssemblyResolver=$resolver
$assembly=[Mono.Cecil.AssemblyDefinition]::ReadAssembly($backupPath,$parameters)
$manager=$assembly.MainModule.Types | Where-Object Name -eq 'NetWebManager'
$method=$manager.Methods | Where-Object Name -eq 'GetLoginParamsFromWin'
$getArgs=($method.Body.Instructions | Where-Object {$_.Operand -is [Mono.Cecil.MethodReference] -and $_.Operand.Name -eq 'GetCommandLineArgs'} | Select-Object -First 1).Operand
$concat=($method.Body.Instructions | Where-Object {$_.Operand -is [Mono.Cecil.MethodReference] -and $_.Operand.Name -eq 'Concat' -and $_.Operand.Parameters.Count -eq 2} | Select-Object -First 1).Operand
$startCoroutine=($method.Body.Instructions | Where-Object {$_.Operand -is [Mono.Cecil.MethodReference] -and $_.Operand.Name -eq 'StartCoroutine'} | Select-Object -First 1).Operand
$sendHttp=$manager.Methods | Where-Object Name -eq 'SendHttpGet'
$method.Body.Instructions.Clear();$method.Body.ExceptionHandlers.Clear();$method.Body.Variables.Clear()
$il=$method.Body.GetILProcessor()
$fallback=[Mono.Cecil.Cil.Instruction]::Create([Mono.Cecil.Cil.OpCodes]::Ldstr,'10001')
$join=[Mono.Cecil.Cil.Instruction]::Create([Mono.Cecil.Cil.OpCodes]::Call,$concat)
$il.Emit([Mono.Cecil.Cil.OpCodes]::Ldarg_0)
$il.Emit([Mono.Cecil.Cil.OpCodes]::Ldarg_0)
$il.Emit([Mono.Cecil.Cil.OpCodes]::Ldstr,($serverBase+'/native/bootstrap?account='))
$il.Emit([Mono.Cecil.Cil.OpCodes]::Call,$getArgs)
$il.Emit([Mono.Cecil.Cil.OpCodes]::Ldlen)
$il.Emit([Mono.Cecil.Cil.OpCodes]::Conv_I4)
$il.Emit([Mono.Cecil.Cil.OpCodes]::Ldc_I4_1)
$il.Emit([Mono.Cecil.Cil.OpCodes]::Ble,$fallback)
$il.Emit([Mono.Cecil.Cil.OpCodes]::Call,$getArgs)
$il.Emit([Mono.Cecil.Cil.OpCodes]::Ldc_I4_1)
$il.Emit([Mono.Cecil.Cil.OpCodes]::Ldelem_Ref)
$il.Emit([Mono.Cecil.Cil.OpCodes]::Br,$join)
$il.Append($fallback);$il.Append($join)
$il.Emit([Mono.Cecil.Cil.OpCodes]::Call,$sendHttp)
$il.Emit([Mono.Cecil.Cil.OpCodes]::Call,$startCoroutine)
$il.Emit([Mono.Cecil.Cil.OpCodes]::Pop)
$il.Emit([Mono.Cecil.Cil.OpCodes]::Ldstr,'')
$il.Emit([Mono.Cecil.Cil.OpCodes]::Ret)
$netType=$assembly.MainModule.Types | Where-Object Name -eq 'NetManager'
$netConstructor=$netType.Methods | Where-Object Name -eq '.ctor'
$networkInstructions=$netConstructor.Body.Instructions
$transportChanged=$false
for($index=1;$index -lt $networkInstructions.Count;$index++) {
    if($networkInstructions[$index].OpCode.Name -eq 'stfld' -and $networkInstructions[$index].Operand.Name -eq 'KcpModel') {
        if($networkInstructions[$index-1].OpCode.Name -ne 'ldc.i4.1') {throw 'KCP默认初始化与核验原文不一致'}
        # 本地适配器使用原版已存在的TCP分支，不修改战斗逻辑。
        $networkInstructions[$index-1].OpCode=[Mono.Cecil.Cil.OpCodes]::Ldc_I4_0
        $transportChanged=$true
    }
}
if(!$transportChanged) {throw '未找到原版传输分支初始化'}
# 本地双客户端调试需要切换窗口，将邀请等待时间延长到五分钟。
$inviteType=$assembly.MainModule.Types | Where-Object Name -eq 'TeamTipView'
$inviteMethod=$inviteType.Methods | Where-Object Name -eq 'redraw'
$inviteInstructions=$inviteMethod.Body.Instructions
$inviteChanged=0
for($index=1;$index -lt $inviteInstructions.Count;$index++) {
    if($inviteInstructions[$index].OpCode.Name -eq 'stfld' -and $inviteInstructions[$index].Operand.Name -eq 'countDownTime') {
        if($inviteInstructions[$index-1].OpCode.Name -ne 'ldc.i4.s' -or [int]$inviteInstructions[$index-1].Operand -ne 9) {throw '邀请倒计时与核验原文不一致'}
        $inviteInstructions[$index-1].OpCode=[Mono.Cecil.Cil.OpCodes]::Ldc_I4
        $inviteInstructions[$index-1].Operand=[int]300
        $inviteChanged++
    }
}
if($inviteChanged -ne 1) {throw '邀请倒计时匹配数量异常'}
# 本地双客户端调试需轮流操作窗口：开启原版 Constant.bNoConverAi，停用挂机转AI及“必须活跃起来”警告。
$constantType=$assembly.MainModule.Types | Where-Object Name -eq 'Constant'
$constantInit=$constantType.Methods | Where-Object Name -eq '.cctor'
$constantInstructions=$constantInit.Body.Instructions
$hangChanged=0
for($index=1;$index -lt $constantInstructions.Count;$index++) {
    if($constantInstructions[$index].OpCode.Name -eq 'stsfld' -and $constantInstructions[$index].Operand.Name -eq 'bNoConverAi') {
        if($constantInstructions[$index-1].OpCode.Name -ne 'ldc.i4.0') {throw '挂机开关初始化与核验原文不一致'}
        $constantInstructions[$index-1].OpCode=[Mono.Cecil.Cil.OpCodes]::Ldc_I4_1
        $hangChanged++
    }
}
if($hangChanged -ne 1) {throw '挂机开关匹配数量异常'}
# 个人混战（BattleType 2）中客户端按出场顺序从0编号队伍；0号与默认伪队伍号0相同，首位玩家的子弹和人物技能被判为友军。
# 改为从1编号：只改 HeroManger.createHero 中该模式的 TeamId，复活点仍用原序号。
$heroMgrType=$assembly.MainModule.Types | Where-Object Name -eq 'HeroManger'
$createHeroMethod=$heroMgrType.Methods | Where-Object {$_.Name -eq 'createHero' -and $_.Parameters.Count -eq 2}
$hi=$createHeroMethod.Body.Instructions
$ffaChanged=0
for($index=2;$index -lt $hi.Count;$index++) {
    if($hi[$index].OpCode.Name -eq 'callvirt' -and $hi[$index].Operand.Name -eq 'set_TeamId' -and $hi[$index-1].OpCode.Name -eq 'call' -and $hi[$index-2].OpCode.Name -eq 'ldarg.2') {
        $hp=$createHeroMethod.Body.GetILProcessor()
        $one=$hp.Create([Mono.Cecil.Cil.OpCodes]::Ldc_I4_1);$plus=$hp.Create([Mono.Cecil.Cil.OpCodes]::Add)
        $hp.InsertAfter($hi[$index-2],$one);$hp.InsertAfter($one,$plus)
        $ffaChanged++;break
    }
}
if($ffaChanged -ne 1) {throw '个人混战队伍编号与核验原文不一致'}
# 本地规则：能源基地(OpenBaseVo Id=23)开放时间改为每天20-24点，与服务器一致；其他模式开放时间不变。
$openType=$assembly.MainModule.Types | Where-Object Name -eq 'OpenBaseVo' | Select-Object -First 1
if(!$openType) {throw '未找到 OpenBaseVo'}
$openFrom=$openType.Methods | Where-Object Name -eq 'fromData'
$openTimeField=$openType.Fields | Where-Object Name -eq 'OpenTime'
$openIdField=$openType.Fields | Where-Object Name -eq 'Id'
$oi=$openFrom.Body.Instructions
$openChanged=0
for($index=0;$index -lt $oi.Count;$index++) {
    if($oi[$index].OpCode.Name -eq 'stfld' -and $oi[$index].Operand.Name -eq 'OpenTime') {
        $op=$openFrom.Body.GetILProcessor()
        $skip=$op.Create([Mono.Cecil.Cil.OpCodes]::Nop)
        $seq=@($op.Create([Mono.Cecil.Cil.OpCodes]::Ldarg_0),$op.Create([Mono.Cecil.Cil.OpCodes]::Ldfld,$openIdField),
               $op.Create([Mono.Cecil.Cil.OpCodes]::Ldc_I4,23),$op.Create([Mono.Cecil.Cil.OpCodes]::Bne_Un,$skip),
               $op.Create([Mono.Cecil.Cil.OpCodes]::Ldarg_0),$op.Create([Mono.Cecil.Cil.OpCodes]::Ldstr,'everyday#20:00-24:00'),
               $op.Create([Mono.Cecil.Cil.OpCodes]::Stfld,$openTimeField),$skip)
        $prev=$oi[$index]
        foreach($ins in $seq) {$op.InsertAfter($prev,$ins);$prev=$ins}
        $openChanged++;break
    }
}
if($openChanged -ne 1) {throw '模式开放时间字段与核验原文不一致'}
# 本地规则（养成号）：限定英雄只要原表有钻石价(Diam>0)就改为可购买(IsBuy=0)，商店直接用钻石买。
$heroType=$assembly.MainModule.Types | Where-Object Name -eq 'HeroBaseVo' | Select-Object -First 1
if(!$heroType) {throw '未找到 HeroBaseVo'}
$heroFrom=$heroType.Methods | Where-Object Name -eq 'fromData'
$heroBuyField=$heroType.Fields | Where-Object Name -eq 'IsBuy'
$heroDiamField=$heroType.Fields | Where-Object Name -eq 'Diam'
$hbi=$heroFrom.Body.Instructions
$heroChanged=0
for($index=0;$index -lt $hbi.Count;$index++) {
    if($hbi[$index].OpCode.Name -eq 'stfld' -and $hbi[$index].Operand.Name -eq 'IsBuy') {
        $hop=$heroFrom.Body.GetILProcessor()
        $hskip=$hop.Create([Mono.Cecil.Cil.OpCodes]::Nop)
        # 限定英雄统一定价（用户指定）：节日英雄（活动获取且原价3688）= 3688；其他限定英雄（活动/专属/军队，含黄中、龙三太子、龙骑士）= 3488；通行证英雄不改
        $heroDisField=$heroType.Fields | Where-Object Name -eq 'DiamDis'
        $heroGoldField=$heroType.Fields | Where-Object Name -eq 'Gold'
        $heroSilverField=$heroType.Fields | Where-Object Name -eq 'Silver'
        if(!$heroGoldField -or !$heroSilverField) {throw '未找到 HeroBaseVo.Gold/Silver'}
        $O=[Mono.Cecil.Cil.OpCodes]
        $pSkip=$hop.Create($O::Nop); $pDis=$hop.Create($O::Ldarg_0); $p2888=$hop.Create($O::Ldarg_0)
        $hpre=@($hop.Create($O::Ldarg_0),$hop.Create($O::Ldfld,$heroBuyField),$hop.Create($O::Ldc_I4_7),$hop.Create($O::Beq,$pSkip),
                $hop.Create($O::Ldarg_0),$hop.Create($O::Ldfld,$heroBuyField),$hop.Create($O::Brfalse,$pSkip),
                $hop.Create($O::Ldarg_0),$hop.Create($O::Ldfld,$heroDiamField),$hop.Create($O::Brfalse,$pSkip),
                $hop.Create($O::Ldarg_0),$hop.Create($O::Ldfld,$heroBuyField),$hop.Create($O::Ldc_I4_3),$hop.Create($O::Bne_Un,$p2888),
                $hop.Create($O::Ldarg_0),$hop.Create($O::Ldfld,$heroDiamField),$hop.Create($O::Ldc_I4,3688),$hop.Create($O::Beq,$pDis),
                $p2888,$hop.Create($O::Ldc_I4,3488),$hop.Create($O::Stfld,$heroDiamField),
                $pDis,$hop.Create($O::Ldarg_0),$hop.Create($O::Ldfld,$heroDiamField),$hop.Create($O::Stfld,$heroDisField),
                $hop.Create($O::Ldarg_0),$hop.Create($O::Ldc_I4_0),$hop.Create($O::Stfld,$heroGoldField),
                $hop.Create($O::Ldarg_0),$hop.Create($O::Ldc_I4_0),$hop.Create($O::Stfld,$heroSilverField),$pSkip)
        # 限定英雄只能用钻石买（用户指定）：金币/银币价清零，商店不再显示“金币 或 钻石”
        # 栈：$pDis 压 this，再压 this.Diam，stfld 消耗两者 —— 平衡
        # 通行证英雄(IsBuy=7)：只能用通行币在通行证商店换，保持原样不改成可购买
        $hseq=$hpre+@($hop.Create([Mono.Cecil.Cil.OpCodes]::Ldarg_0),$hop.Create([Mono.Cecil.Cil.OpCodes]::Ldfld,$heroBuyField),
                $hop.Create([Mono.Cecil.Cil.OpCodes]::Ldc_I4_7),$hop.Create([Mono.Cecil.Cil.OpCodes]::Beq,$hskip),
                $hop.Create([Mono.Cecil.Cil.OpCodes]::Ldarg_0),$hop.Create([Mono.Cecil.Cil.OpCodes]::Ldfld,$heroDiamField),
                $hop.Create([Mono.Cecil.Cil.OpCodes]::Brfalse,$hskip),
                $hop.Create([Mono.Cecil.Cil.OpCodes]::Ldarg_0),$hop.Create([Mono.Cecil.Cil.OpCodes]::Ldc_I4_0),
                $hop.Create([Mono.Cecil.Cil.OpCodes]::Stfld,$heroBuyField),$hskip)
        $hprev=$hbi[$index]
        foreach($ins in $hseq) {$hop.InsertAfter($hprev,$ins);$hprev=$ins}
        $heroChanged++;break
    }
}
if($heroChanged -ne 1) {throw '英雄可购买字段与核验原文不一致'}
# 禁选/选人界面：空位条目（没有玩家数据）收到“想禁/想选”广播时原版会空引用报错，入口加空值判断直接返回。
$banItemType=$assembly.MainModule.Types | Where-Object Name -eq 'BattleBanUserItem' | Select-Object -First 1
if(!$banItemType) {throw '未找到 BattleBanUserItem'}
$banSwitch=$banItemType.Methods | Where-Object Name -eq 'onBattleBanPickSwitch' | Select-Object -First 1
$banVoField=$banItemType.Fields | Where-Object Name -eq 'battleBanUserItemVo' | Select-Object -First 1
if(!$banSwitch -or !$banVoField) {throw '禁选条目方法与核验原文不一致'}
$bop=$banSwitch.Body.GetILProcessor(); $O=[Mono.Cecil.Cil.OpCodes]
$bfirst=$banSwitch.Body.Instructions[0]
foreach($ins in @($bop.Create($O::Ldarg_0),$bop.Create($O::Ldfld,$banVoField),$bop.Create($O::Brtrue,$bfirst),$bop.Create($O::Ret))) {$bop.InsertBefore($bfirst,$ins)}
# 大厅“违规举报”按钮：原版用 Application.ExternalCall（网页版才有效，微端点了没反应），改为 Utils.jumpToURL（系统浏览器打开）→ 本地争议对局查看页
# 直接借用程序里现成的 UnityEngine.Application::OpenURL(string) 调用引用（Utils.jumpToURL 内部就是它）
$openUrlRef=$null
foreach($t in $assembly.MainModule.Types) {
    foreach($m in $t.Methods) {
        if(!$m.HasBody) {continue}
        foreach($x in $m.Body.Instructions) {
            if($x.OpCode.Name -eq 'call' -and $x.Operand -and $x.Operand.Name -eq 'OpenURL' -and $x.Operand.DeclaringType.Name -eq 'Application') {$openUrlRef=$x.Operand; break}
        }
        if($openUrlRef) {break}
    }
    if($openUrlRef) {break}
}
if(!$openUrlRef) {throw '未找到 Application.OpenURL'}
$indexType=$assembly.MainModule.Types | Where-Object Name -eq 'IndexSceneView' | Select-Object -First 1
$reportChanged=0
foreach($m in $indexType.Methods) {
    if(!$m.HasBody) {continue}
    $ins=$m.Body.Instructions
    for($i=1;$i -lt $ins.Count;$i++) {
        if($ins[$i].OpCode.Name -eq 'call' -and $ins[$i].Operand.Name -eq 'jumpToURLWithNewWindow' -and $ins[$i-1].OpCode.Name -eq 'ldstr' -and ([string]$ins[$i-1].Operand).Contains('/zhuanti/other/wgjb')) {
            $ins[$i].Operand=$openUrlRef; $reportChanged++
        }
    }
}
if($reportChanged -lt 1) {throw '违规举报按钮与核验原文不一致'}
# 皮肤：保持原版（节日/通行证/排位/赛事限定皮肤在商店显示获取途径，不能钻石购买）。
# 例外（用户指定）：小哪咤·庚鼠(217)、小司马·虎虎生风(336) 原版无获取途径，改为商店 1288 钻可买；巳巳如意下线后其 3 款节日皮肤 嬴正·岚炎霸少(295)、姬小发·暮影先锋(366)、姬轩辕·龙吟九天(369) 同样 1288 钻可买。
$skinType=$assembly.MainModule.Types | Where-Object Name -eq 'SkinIdBaseVo' | Select-Object -First 1
if(!$skinType) {throw '未找到 SkinIdBaseVo'}
$skinFrom=$skinType.Methods | Where-Object Name -eq 'fromData'
$skinIdField=$skinType.Fields | Where-Object Name -eq 'Id'
$skinTypeField=$skinType.Fields | Where-Object Name -eq 'Type'
$skinNumField=$skinType.Fields | Where-Object Name -eq 'Num'
$ski=$skinFrom.Body.Instructions
$skinChanged=0
for($index=0;$index -lt $ski.Count;$index++) {
    if($ski[$index].OpCode.Name -eq 'stfld' -and $ski[$index].Operand.Name -eq 'GetWay') {
        $sop=$skinFrom.Body.GetILProcessor(); $O=[Mono.Cecil.Cil.OpCodes]
        $sskip=$sop.Create($O::Nop); $sset=$sop.Create($O::Ldarg_0)
        $sseq=@()
        foreach($sid in @(217,336,295,366,369)) {$sseq+=@($sop.Create($O::Ldarg_0),$sop.Create($O::Ldfld,$skinIdField),$sop.Create($O::Ldc_I4,$sid),$sop.Create($O::Beq,$sset))}
        $sseq+=@($sop.Create($O::Br,$sskip),
                $sset,$sop.Create($O::Ldc_I4,1288),$sop.Create($O::Stfld,$skinNumField),
                $sop.Create($O::Ldarg_0),$sop.Create($O::Ldc_I4,3),$sop.Create($O::Stfld,$skinTypeField),$sskip)
        $sprev=$ski[$index]
        foreach($ins in $sseq) {$sop.InsertAfter($sprev,$ins);$sprev=$ins}
        $skinChanged++;break
    }
}
if($skinChanged -ne 1) {throw '皮肤获取途径字段与核验原文不一致'}
# 本地规则（用户指定）：只取消“限时活动”和“星卡(LuckV2View)”的“今日抽奖次数 x/50”限制；专属商店、抽英雄等其他抽奖保持原限制。
# 1) 凡是含“当日抽奖次数不足！”提示的方法，把其中读取抽奖次数(drawNum 字段 / get_DrawNum)的地方一律当作 0；
# 2) 所有 get_DrawNum 返回 0；3) 显示文字“{0}/50”改成“{0}/不限”。
$drawChanged=0
$drawTypes='^(LimitActivity|GeneralV3|LuckV2View|LookForPerson|WolveDoc|TurnTableTwo|HappyCrane|LuckWheelV2|Arrow)'
foreach($t in $assembly.MainModule.Types) {
    if($t.Name -notmatch $drawTypes) {continue}
    foreach($m in $t.Methods) {
        if(!$m.HasBody) {continue}
        $ins=$m.Body.Instructions
        $hasTip=$false
        foreach($x in $ins) { if($x.OpCode.Name -eq 'ldstr' -and $x.Operand -eq '当日抽奖次数不足！') {$hasTip=$true} }
        if($m.Name -eq 'get_DrawNum' -and $m.HasThis -and $m.ReturnType.Name -eq 'Int32' -and $t.Name -notmatch 'Response$|Request$') {
            $p0=$m.Body.GetILProcessor(); $f0=$ins[0]
            $p0.InsertBefore($f0,$p0.Create([Mono.Cecil.Cil.OpCodes]::Ldc_I4_0)); $p0.InsertBefore($f0,$p0.Create([Mono.Cecil.Cil.OpCodes]::Ret))
            $drawChanged++; continue
        }
        $pp=$m.Body.GetILProcessor()
        for($i=$ins.Count-1;$i -ge 0;$i--) {
            $x=$ins[$i]
            if($x.OpCode.Name -eq 'ldstr' -and $x.Operand -is [string] -and $x.Operand.StartsWith('今日抽奖次数') -and $x.Operand.EndsWith('/50')) {
                $x.Operand=$x.Operand.Substring(0,$x.Operand.Length-3)+'/不限'; $drawChanged++; continue
            }
            if(!$hasTip) {continue}
            $isField = ($x.OpCode.Name -eq 'ldfld' -and $x.Operand.Name -match '(?i)^drawnum$')
            $isCall = (($x.OpCode.Name -eq 'callvirt' -or $x.OpCode.Name -eq 'call') -and $x.Operand.Name -match '(?i)^get_drawnum$' -and $x.Operand.HasThis -and $x.Operand.Parameters.Count -eq 0)
            if($isField -or $isCall) {
                $x.OpCode=[Mono.Cecil.Cil.OpCodes]::Pop; $x.Operand=$null
                $pp.InsertAfter($x,$pp.Create([Mono.Cecil.Cil.OpCodes]::Ldc_I4_0)); $drawChanged++
            }
        }
    }
}
if($drawChanged -lt 1) {throw '抽奖次数限制方法与核验原文不一致'}
if($BattleReport) {
    # 调试开关：让原版 BattleReport.IsNeedAddReport 恒为真，对局结束时 endGame 写出 battleReport_<uid>_<battleId>。
    $reportType=$assembly.MainModule.Types | Where-Object Name -eq 'BattleReport'
    $reportMethod=$reportType.Methods | Where-Object Name -eq 'IsNeedAddReport'
    $reportMethod.Body.Instructions.Clear();$reportMethod.Body.ExceptionHandlers.Clear()
    $reportIl=$reportMethod.Body.GetILProcessor()
    $reportIl.Emit([Mono.Cecil.Cil.OpCodes]::Ldc_I4_1);$reportIl.Emit([Mono.Cecil.Cil.OpCodes]::Ret)
    # writeReport 原版只在回放模式写文件：把开头的 brfalse 改成 pop，让普通对局结束时也写出日志。
    $writeMethod=$reportType.Methods | Where-Object Name -eq 'writeReport'
    $firstBranch=$writeMethod.Body.Instructions[2]
    if($writeMethod.Body.Instructions[1].Operand.Name -ne 'BPlaybackSystemMode' -or $firstBranch.OpCode.Name -ne 'brfalse') {throw '战斗日志写出入口与核验原文不一致'}
    $firstBranch.OpCode=[Mono.Cecil.Cil.OpCodes]::Pop;$firstBranch.Operand=$null
    # 日志上限原为1000条，只够最后十几秒；调大以覆盖整局。
    $addMethod=$reportType.Methods | Where-Object Name -eq 'addReport'
    $capItem=$addMethod.Body.Instructions | Where-Object {$_.OpCode.Name -eq 'ldc.i4' -and [int]$_.Operand -eq 1000}
    if(@($capItem).Count -ne 1) {throw '战斗日志上限与核验原文不一致'}
    $capItem.Operand=[int]5000000
    # 在英雄碰撞入口（子弹类型转换之后）记录"被碰英雄|子弹主人"，判断A的子弹是否发生碰撞。
    $heroCtrlType=$assembly.MainModule.Types | Where-Object Name -eq 'HeroController'
    $contact=$heroCtrlType.Methods | Where-Object Name -eq 'ContactBeginEx'
    $ci=$contact.Body.Instructions
    $tGm=($ci | Where-Object {$_.OpCode.Name -eq 'ldsfld' -and $_.Operand.Name -eq 'gameManager'} | Select-Object -First 1).Operand
    $tBr=($ci | Where-Object {$_.OpCode.Name -eq 'ldfld' -and $_.Operand.Name -eq 'battleReport'} | Select-Object -First 1).Operand
    $tUid=($ci | Where-Object {$_.OpCode.Name -eq 'ldfld' -and $_.Operand.Name -eq 'uid'} | Select-Object -First 1).Operand
    $tStr=($ci | Where-Object {$_.OpCode.Name -eq 'call' -and $_.Operand.Name -eq 'op_Implicit' -and $_.Operand.DeclaringType.Name -eq 'ObscuredString'} | Select-Object -First 1).Operand
    $tAdd=($ci | Where-Object {$_.Operand -is [Mono.Cecil.MethodReference] -and $_.Operand.Name -eq 'addReport'} | Select-Object -First 1).Operand
    $tOwner=($ci | Where-Object {$_.Operand -is [Mono.Cecil.MethodReference] -and $_.Operand.Name -eq 'get_Owner'} | Select-Object -First 1).Operand
    $anchorIns=$ci | Where-Object {$_.OpCode.Name -eq 'stloc.1'} | Select-Object -First 1
    if(-not ($tGm -and $tBr -and $tUid -and $tStr -and $tAdd -and $tOwner -and $anchorIns)) {throw '碰撞日志注入点与核验原文不一致'}
    $cil=$contact.Body.GetILProcessor()
    $OC=[Mono.Cecil.Cil.OpCodes]
    $tTeam=($ci | Where-Object {$_.Operand -is [Mono.Cecil.MethodReference] -and $_.Operand.Name -eq 'get_TeamId' -and $_.Operand.DeclaringType.Name -eq 'HeroBase'} | Select-Object -First 1).Operand
    $tFake=($ci | Where-Object {$_.Operand -is [Mono.Cecil.MethodReference] -and $_.Operand.Name -eq 'get_FakeTeamId'} | Select-Object -First 1).Operand
    $tInt=($ci | Where-Object {$_.OpCode.Name -eq 'call' -and $_.Operand.Name -eq 'op_Implicit' -and $_.Operand.DeclaringType.Name -eq 'ObscuredInt' -and $_.Operand.ReturnType.Name -eq 'Int32'} | Select-Object -First 1).Operand
    $tBulletTeam=($ci | Where-Object {$_.Operand -is [Mono.Cecil.MethodReference] -and $_.Operand.Name -eq 'getTeamId'} | Select-Object -First 1).Operand
    if(-not ($tTeam -and $tFake -and $tInt -and $tBulletTeam)) {throw '队伍字段注入引用缺失'}
    $int32Type=$assembly.MainModule.TypeSystem.Int32
    # 复用程序集内已有的 String.Concat(object[]) 引用，避免引入宿主 .NET 版本的 mscorlib。
    $heroAttrType=$assembly.MainModule.Types | Where-Object Name -eq 'HeroAttr'
    $concat=(($heroAttrType.Methods | Where-Object Name -eq 'checkZB').Body.Instructions | Where-Object {$_.Operand -is [Mono.Cecil.MethodReference] -and $_.Operand.Name -eq 'Concat' -and $_.Operand.Parameters.Count -eq 1 -and $_.Operand.Parameters[0].ParameterType.FullName -eq 'System.Object[]'} | Select-Object -First 1).Operand
    if(-not $concat) {throw 'Concat引用缺失'}
    $objType=$assembly.MainModule.TypeSystem.Object
    # 记录：被碰英雄 | 子弹主人uid, 子弹队伍, 被碰英雄队伍, 被碰英雄伪队伍
    $seq=@($cil.Create($OC::Ldsfld,$tGm),$cil.Create($OC::Ldfld,$tBr),$cil.Create($OC::Ldarg_0),$cil.Create($OC::Ldfld,$tUid),$cil.Create($OC::Call,$tStr),
           $cil.Create($OC::Ldc_I4_8),$cil.Create($OC::Newarr,$objType),
           $cil.Create($OC::Dup),$cil.Create($OC::Ldc_I4_0),$cil.Create($OC::Ldloc_1),$cil.Create($OC::Callvirt,$tOwner),$cil.Create($OC::Ldfld,$tUid),$cil.Create($OC::Call,$tStr),$cil.Create($OC::Stelem_Ref),
           $cil.Create($OC::Dup),$cil.Create($OC::Ldc_I4_1),$cil.Create($OC::Ldstr,' bT:'),$cil.Create($OC::Stelem_Ref),
           $cil.Create($OC::Dup),$cil.Create($OC::Ldc_I4_2),$cil.Create($OC::Ldloc_1),$cil.Create($OC::Callvirt,$tBulletTeam),$cil.Create($OC::Box,$int32Type),$cil.Create($OC::Stelem_Ref),
           $cil.Create($OC::Dup),$cil.Create($OC::Ldc_I4_3),$cil.Create($OC::Ldstr,' myT:'),$cil.Create($OC::Stelem_Ref),
           $cil.Create($OC::Dup),$cil.Create($OC::Ldc_I4_4),$cil.Create($OC::Ldarg_0),$cil.Create($OC::Call,$tTeam),$cil.Create($OC::Call,$tInt),$cil.Create($OC::Box,$int32Type),$cil.Create($OC::Stelem_Ref),
           $cil.Create($OC::Dup),$cil.Create($OC::Ldc_I4_5),$cil.Create($OC::Ldstr,' myF:'),$cil.Create($OC::Stelem_Ref),
           $cil.Create($OC::Dup),$cil.Create($OC::Ldc_I4_6),$cil.Create($OC::Ldarg_0),$cil.Create($OC::Call,$tFake),$cil.Create($OC::Call,$tInt),$cil.Create($OC::Box,$int32Type),$cil.Create($OC::Stelem_Ref),
           $cil.Create($OC::Dup),$cil.Create($OC::Ldc_I4_7),$cil.Create($OC::Ldstr,' contact'),$cil.Create($OC::Stelem_Ref),
           $cil.Create($OC::Call,$concat),$cil.Create($OC::Callvirt,$tAdd))
    $prev=$anchorIns
    foreach($x in $seq){ $cil.InsertAfter($prev,$x); $prev=$x }
}
function Get-AllClientTypes($types) { foreach($type in $types) { $type; if($type.HasNestedTypes) { Get-AllClientTypes $type.NestedTypes } } }
$redirectCount=0
# 项目副本的平台请求统一指向本地适配器，避免虚拟账号访问官方平台。
foreach($type in (Get-AllClientTypes $assembly.MainModule.Types)) {
    foreach($clientMethod in $type.Methods) {
        if(!$clientMethod.HasBody) {continue}
        foreach($instruction in $clientMethod.Body.Instructions) {
            if($instruction.OpCode.Name -eq 'ldstr' -and $instruction.Operand -match '^https?://' -and $instruction.Operand -notlike ($serverBase+'/*')) {
                $instruction.Operand=[regex]::Replace($instruction.Operand,'^https?://[^/]+',$serverBase)
                $redirectCount++
            }
        }
    }
}
$outputPath=Join-Path $backupRoot '1010_Assembly-CSharp本地_v1.dll'
$assembly.Write($outputPath);$assembly.Dispose()
Copy-Item -LiteralPath $outputPath -Destination $assemblyPath
[ordered]@{original_sha256=$originalHash;patched_sha256=(Get-FileHash $assemblyPath).Hash.ToLower();target=$assemblyPath;server_ip=$ServerIp;patch_script_sha256=(Get-FileHash -LiteralPath $PSCommandPath).Hash.ToLower();changes=@('NetWebManager.GetLoginParamsFromWin：仅从127.0.0.1获取本地账号启动参数','项目副本HTTP平台地址改为本机适配','NetManager.KcpModel=false：本地战斗使用原版已有TCP分支');redirected_literal_count=$redirectCount;resource_checks_preserved=$true;runtime_verified=$false} | ConvertTo-Json -Depth 5 | Set-Content $recordPath -Encoding utf8
$patchRecord=Get-Content $recordPath -Raw | ConvertFrom-Json
$patchRecord | Add-Member -NotePropertyName invitation_timeout_seconds -NotePropertyValue 300
$patchRecord | Add-Member -NotePropertyName afk_convert_ai_disabled -NotePropertyValue $true
$patchRecord | Add-Member -NotePropertyName ffa_team_id_from_one -NotePropertyValue $true
$patchRecord | Add-Member -NotePropertyName energy_base_open_20_24 -NotePropertyValue $true
$patchRecord | Add-Member -NotePropertyName battle_report_debug -NotePropertyValue ([bool]$BattleReport)
$patchRecord | ConvertTo-Json -Depth 5 | Set-Content $recordPath -Encoding utf8
Write-Output '项目副本已改为本地启动；邀请等待时间300秒；已停用挂机转AI与警告，运行验证尚未完成'
