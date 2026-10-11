function Test-ReaClientPatch {
    param([string]$ClientRoot,[string]$RecordPath,[string]$ServerIp,[string]$PatchScript)
    $assemblyPath=Join-Path $ClientRoot 'hdjt_Data/Managed/Assembly-CSharp.dll'
    if(!(Test-Path -LiteralPath $RecordPath) -or !(Test-Path -LiteralPath $assemblyPath)) {return $false}
    try {
        $record=Get-Content -LiteralPath $RecordPath -Raw -Encoding UTF8 | ConvertFrom-Json
        return $record.server_ip -eq $ServerIp -and $record.patched_sha256 -eq (Get-FileHash -LiteralPath $assemblyPath).Hash.ToLower() -and $record.patch_script_sha256 -eq (Get-FileHash -LiteralPath $PatchScript).Hash.ToLower()
    } catch {return $false}
}
