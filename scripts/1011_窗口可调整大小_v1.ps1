# 让游戏窗口可以拖边框自由调整大小、可最大化。只改窗口样式，不改游戏文件。
# 用法：powershell -File 1011_窗口可调整大小_v1.ps1 [-ProcessId <PID>] [-Name hdjt]
param([int]$ProcessId = 0, [string]$Name = '', [int]$WaitSeconds = 90)
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class WinStyle {
    [DllImport("user32.dll", SetLastError=true)] public static extern int GetWindowLong(IntPtr h, int i);
    [DllImport("user32.dll", SetLastError=true)] public static extern int SetWindowLong(IntPtr h, int i, int v);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr a, int x, int y, int cx, int cy, uint f);
}
"@
$GWL_STYLE = -16; $WS_THICKFRAME = 0x00040000; $WS_MAXIMIZEBOX = 0x00010000
$SWP_FLAGS = 0x0027  # NOSIZE|NOMOVE|NOZORDER|FRAMECHANGED
# 游戏切换场景/分辨率时会把样式改回去，所以持续守护：只要游戏还开着，每 2 秒检查一次，缺边框就补回。
$deadline = (Get-Date).AddSeconds($WaitSeconds)
$seen = $false
while ($true) {
    $procs = if ($ProcessId) { @(Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) } elseif ($Name) { @(Get-Process -Name $Name -ErrorAction SilentlyContinue) } else { @(Get-Process -Name hdjt, hdjtB -ErrorAction SilentlyContinue) }
    if ($procs.Count) { $seen = $true } elseif ($seen -or (Get-Date) -gt $deadline) { break }
    foreach ($p in $procs) {
        $h = $p.MainWindowHandle
        if ($h -eq [IntPtr]::Zero) { continue }
        $s = [WinStyle]::GetWindowLong($h, $GWL_STYLE)
        if (($s -band $WS_THICKFRAME) -eq 0) {
            [void][WinStyle]::SetWindowLong($h, $GWL_STYLE, ($s -bor $WS_THICKFRAME -bor $WS_MAXIMIZEBOX))
            [void][WinStyle]::SetWindowPos($h, [IntPtr]::Zero, 0, 0, 0, 0, $SWP_FLAGS)
        }
    }
    Start-Sleep -Seconds 2
}
