function Test-ReaNetworkAddress {
    param([string]$Address)
    $parsed=$null
    if(![Net.IPAddress]::TryParse($Address,[ref]$parsed) -or $parsed.AddressFamily -ne 'InterNetwork') {return $false}
    $bytes=$parsed.GetAddressBytes()
    $policy=Get-Content -LiteralPath (Join-Path $PSScriptRoot '1011_网络规则_v1.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach($cidr in $policy.allowedIpv4Cidrs) {
        $parts=$cidr.Split('/')
        $network=[Net.IPAddress]::Parse($parts[0]).GetAddressBytes()
        $remaining=[int]$parts[1]
        if($remaining -lt 0 -or $remaining -gt 32) {throw '网络配置前缀无效'}
        $matches=$true
        foreach($index in 0..3) {
            $bits=[Math]::Min(8,[Math]::Max(0,$remaining))
            $mask=(255 -shl (8-$bits)) -band 255
            if(($bytes[$index] -band $mask) -ne ($network[$index] -band $mask)) {$matches=$false;break}
            $remaining-=8
        }
        if($matches) {return $true}
    }
    return $false
}
