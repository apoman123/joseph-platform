# TradeX: open a free MetaQuotes demo account by filling MetaTrader 5's own "Open an Account" window.
# The account is only used to READ charts. Placeholder details are typed (no personal data is sent).
# Called by tradex_setup.py (step mt5_login, mode "demo"). Prints one JSON line: {"ok": bool, "step": "...", "msg": "..."}
#   powershell -ExecutionPolicy Bypass -File setup\mt5_demo.ps1 -ProcessId <terminal64 pid> [-TimeoutSec 120]
# Tested 2026-11-24 on MT5 build 5.0 (MetaQuotes Ltd. -> demo -> details -> "New account opened" -> Finish).
param([int]$ProcessId, [int]$TimeoutSec = 120)
$ErrorActionPreference = "Stop"
Add-Type @"
using System; using System.Collections.Generic; using System.Runtime.InteropServices; using System.Text;
public class TxWin {
  public delegate bool CB(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(CB cb, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr p, CB cb, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr h);
  [DllImport("user32.dll")] public static extern int GetDlgCtrlID(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, string l);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint f);
  public struct RECT { public int L, T, R, B; }
  public static string Text(IntPtr h) { var s = new StringBuilder(512); GetWindowText(h, s, 512); return s.ToString(); }
  public static IntPtr Dialog(uint pid) { IntPtr r = IntPtr.Zero; EnumWindows((h, l) => { uint p; GetWindowThreadProcessId(h, out p);
      if (p == pid && IsWindowVisible(h) && Text(h).StartsWith("Open an Account")) { r = h; return false; } return true; }, IntPtr.Zero); return r; }
  public static IntPtr Ctl(IntPtr dlg, int id) { IntPtr r = IntPtr.Zero; EnumChildWindows(dlg, (h, l) => {
      if (GetDlgCtrlID(h) == id && IsWindowVisible(h)) { r = h; return false; } return true; }, IntPtr.Zero); return r; }
}
"@
function Out($ok, $step, $msg) { (@{ ok = $ok; step = $step; msg = $msg } | ConvertTo-Json -Compress); exit ([int](-not $ok)) }
function Wait-Ctl($id, $sec = 20) {   # a page is ready when its control is visible
  $t = Get-Date
  while (((Get-Date) - $t).TotalSeconds -lt $sec) { $c = [TxWin]::Ctl($script:dlg, $id); if ($c -ne [IntPtr]::Zero) { return $c }; Start-Sleep -Milliseconds 400 }
  return [IntPtr]::Zero
}
function Click($id) { $c = Wait-Ctl $id 10; if ($c -eq [IntPtr]::Zero) { return $false }; [void][TxWin]::PostMessage($c, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero); Start-Sleep -Milliseconds 1500; return $true }
function SetText($id, $v) { $c = Wait-Ctl $id 10; if ($c -eq [IntPtr]::Zero) { return $false }; [void][TxWin]::SendMessage($c, 0x000C, [IntPtr]::Zero, $v); return $true }
function NextEnabled { $c = [TxWin]::Ctl($script:dlg, 12324); return ($c -ne [IntPtr]::Zero -and [TxWin]::IsWindowEnabled($c)) }

# 0. MT5 shows the window on its first start
$t0 = Get-Date; $script:dlg = [IntPtr]::Zero
while (((Get-Date) - $t0).TotalSeconds -lt $TimeoutSec -and $script:dlg -eq [IntPtr]::Zero) { $script:dlg = [TxWin]::Dialog([uint32]$ProcessId); Start-Sleep -Milliseconds 500 }
if ($script:dlg -eq [IntPtr]::Zero) { Out $false "window" "MetaTrader 5 did not show its 'Open an Account' window" }
# Nothing for the person to see or click: move the form off-screen (SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
[void][TxWin]::SetWindowPos($script:dlg, [IntPtr]::Zero, -32000, -32000, 0, 0, 0x0001 -bor 0x0004 -bor 0x0010)

# 1. List of companies: MetaQuotes Ltd. is the first and selected entry -> Next
if (-not (Wait-Ctl 10729)) { Out $false "companies" "company list not found" }
if (-not (Click 12324)) { Out $false "companies" "Next button not found" }

# 2. Open an account: "Open a demo account" -> Next
$demo = Wait-Ctl 10468
if ($demo -eq [IntPtr]::Zero) { Out $false "type" "demo option not found" }
[void][TxWin]::SendMessage($demo, 0x00F1, [IntPtr]1, [IntPtr]::Zero)
if (-not (Click 12324)) { Out $false "type" "Next button not found" }

# 3. Details: placeholders only (MT5 may pre-fill the PC owner's email: it is replaced)
if ((Wait-Ctl 10022) -eq [IntPtr]::Zero) { Out $false "details" "details page not found" }
$ok = (SetText 10022 "TradeX") -and (SetText 10728 "User") -and (SetText 10032 "tradex.user@example.com") -and (SetText 10031 "1712345678")
if (-not $ok) { Out $false "details" "a details field was not found" }
$agree = Wait-Ctl 11106
[void][TxWin]::SendMessage($agree, 0x00F1, [IntPtr]1, [IntPtr]::Zero); [void][TxWin]::PostMessage($agree, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero)
Start-Sleep -Milliseconds 600
if ([int][TxWin]::SendMessage($agree, 0x00F0, [IntPtr]::Zero, [IntPtr]::Zero) -ne 1) { [void][TxWin]::PostMessage($agree, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero) }   # BM_GETCHECK

# Date of birth: put the cursor in the YEAR part (its position follows the PC's short date format) and type 1990
$dob = Wait-Ctl 10732
if ($dob -ne [IntPtr]::Zero) {
  $fmt = (Get-ItemProperty "HKCU:\Control Panel\International").sShortDate
  $parts = ($fmt -split "[^dMyg]+") | Where-Object { $_ -match "^[dMy]" }
  $yi = 0; for ($i = 0; $i -lt $parts.Count; $i++) { if ($parts[$i] -like "y*") { $yi = $i } }
  [void][TxWin]::PostMessage($dob, 0x0201, [IntPtr]1, [IntPtr](10 * 65536 + 6)); [void][TxWin]::PostMessage($dob, 0x0202, [IntPtr]0, [IntPtr](10 * 65536 + 6))
  Start-Sleep -Milliseconds 200
  for ($i = 0; $i -lt $yi; $i++) { [void][TxWin]::PostMessage($dob, 0x0100, [IntPtr]0x27, [IntPtr]1); [void][TxWin]::PostMessage($dob, 0x0101, [IntPtr]0x27, [IntPtr]1); Start-Sleep -Milliseconds 120 }
  foreach ($ch in "1990".ToCharArray()) { [void][TxWin]::PostMessage($dob, 0x0102, [IntPtr][int]$ch, [IntPtr]1); Start-Sleep -Milliseconds 120 }
  Start-Sleep -Milliseconds 800
}
if (-not (NextEnabled)) { Out $false "details" "MetaTrader 5 did not accept the details (Next stayed grey)" }
[void](Click 12324)

# 4. Result page: "New account opened" -> Finish (TradeX never reads or stores the passwords shown there)
$st = Wait-Ctl 10108 40
$status = if ($st -ne [IntPtr]::Zero) { [TxWin]::Text($st) } else { "" }
if ($status -notmatch "opened") { Out $false "result" "MetaQuotes did not confirm a new account ($status)" }
if (-not (Click 12325)) { Out $false "finish" "Finish button not found" }
Out $true "done" "MetaQuotes-Demo account opened"
