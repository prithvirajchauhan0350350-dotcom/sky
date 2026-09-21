param([string]$TitleNeedle = "SKY-IRON-SMOKE", [string]$OutPath = "C:\Users\Svelt\sky\data\iron_smoke.png")

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class WinEnum {
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lp);
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lp);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int cmd);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    public static IntPtr Found = IntPtr.Zero;
    public static string Needle = "";
    public static bool Cb(IntPtr h, IntPtr lp) {
        if (!IsWindowVisible(h)) return true;
        var sb = new StringBuilder(512); GetWindowText(h, sb, 512);
        if (sb.ToString().IndexOf(Needle, StringComparison.OrdinalIgnoreCase) >= 0) { Found = h; return false; }
        return true;
    }
    public static IntPtr Find(string needle) { Needle = needle; Found = IntPtr.Zero; EnumWindows(Cb, IntPtr.Zero); return Found; }
}
"@
$h = [WinEnum]::Find($TitleNeedle)
if ($h -eq [IntPtr]::Zero) { Write-Output "NO_WINDOW_FOUND"; exit 1 }
[WinEnum]::ShowWindow($h, 9) | Out-Null
[WinEnum]::SetForegroundWindow($h) | Out-Null
Start-Sleep -Milliseconds 1600
$r = New-Object WinEnum+RECT
[WinEnum]::GetWindowRect($h, [ref]$r) | Out-Null
$w = $r.Right - $r.Left; $ht = $r.Bottom - $r.Top
Write-Output ("HWND " + $h + " RECT " + $r.Left + "," + $r.Top + " " + $w + "x" + $ht)
if ($w -lt 50 -or $ht -lt 50) { Write-Output "BAD_RECT"; exit 1 }
$bmp = New-Object System.Drawing.Bitmap($w, $ht)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.Left, $r.Top, 0, 0, $bmp.Size)
$bmp.Save($OutPath, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output ("SHOT_OK " + $OutPath)
