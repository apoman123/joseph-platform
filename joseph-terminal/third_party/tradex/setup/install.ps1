# TradeX installer for Windows (no admin needed).
#   powershell -ExecutionPolicy Bypass -c "irm https://github.com/saifullahshafin/open-source-tradingview/releases/latest/download/install.ps1 | iex"
# Options (when run as a file): -Dir D:\TradeX  -Source <folder or .zip of TradeX>  -NoMT5  -Mt5Mode demo|own|skip
# Everything goes into ONE folder: private Python, packages, caches, MetaTrader 5 (portable) and your data.
param([string]$Dir = "", [string]$Source = "", [switch]$NoMT5, [int]$LoginWait = 0, [string]$Mt5Mode = "")
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
function Say($m) { Write-Host "  > $m" -ForegroundColor Cyan }

# 1. Folder: the drive with room, D: first (many laptops have a nearly full C:).
if (-not $Dir) {
  $d = Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Name -eq "D" -and $_.Free -gt 2GB }
  $Dir = if ($d) { "D:\TradeX" } else { Join-Path $env:LOCALAPPDATA "TradeX" }
}
New-Item -ItemType Directory -Force -Path $Dir, "$Dir\runtime\uv", "$Dir\runtime\tmp" | Out-Null
Say "Installing TradeX into $Dir"
$env:TEMP = "$Dir\runtime\tmp"; $env:TMP = $env:TEMP
$env:UV_CACHE_DIR = "$Dir\runtime\uv-cache"; $env:UV_PYTHON_INSTALL_DIR = "$Dir\runtime\python"; $env:UV_TOOL_DIR = "$Dir\runtime\uv-tools"

function Fetch($url, $out) {
  # Windows curl.exe uses the Windows certificate store and resumes cleanly; Invoke-WebRequest is the fallback.
  if (Get-Command curl.exe -ErrorAction SilentlyContinue) { & curl.exe -L --fail --retry 3 -s -o $out $url; if ($LASTEXITCODE -eq 0) { return } }
  Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $out
}

# 2. uv (one small exe that brings its own Python).
$uv = "$Dir\runtime\uv\uv.exe"
if (-not (Test-Path $uv)) {
  $arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "aarch64" } else { "x86_64" }
  Say "Getting uv ($arch)"
  $zip = "$Dir\runtime\tmp\uv.zip"
  Fetch "https://github.com/astral-sh/uv/releases/latest/download/uv-$arch-pc-windows-msvc.zip" $zip
  Expand-Archive -Force $zip "$Dir\runtime\uv"
  Remove-Item $zip
}

# 3. App files (user data in data\ is never touched).
if (-not $Source) {
  Say "Downloading TradeX"
  $Source = "$Dir\runtime\tmp\tradex.zip"
  Fetch "https://github.com/saifullahshafin/open-source-tradingview/archive/refs/heads/main.zip" $Source
}
$stage = "$Dir\runtime\tmp\app"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
if ($Source.EndsWith(".zip")) {
  Expand-Archive -Force $Source $stage
  $root = (Get-ChildItem $stage -Directory | Select-Object -First 1).FullName
} else { $root = (Resolve-Path $Source).Path }
$skip = @(".git", ".venv", "data", "runtime", "mt5", ".env", "__pycache__", "private")  # private/ = owner-only extras, never copied
Get-ChildItem -Force $root | Where-Object { $skip -notcontains $_.Name -and $_.Name -notlike "*.bak_*" } | ForEach-Object {
  Copy-Item -Recurse -Force $_.FullName $Dir
}
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }

# 4. Every other step (Python, packages, sources, MetaTrader 5, shortcut) is done by the shared step engine.
Say "Setting up (Python, packages, chart sources$(if (-not $NoMT5) {', MetaTrader 5'}))"
$args2 = @("run", "--no-project", "--python", "3.12", "python", "$Dir\setup\tradex_setup.py", "--all")
if ($NoMT5) { $args2 += "--no-mt5" }
if ($LoginWait -gt 0) { $args2 += @("--login-wait", "$LoginWait") }
if ($Mt5Mode) { $args2 += @("--mt5-mode", $Mt5Mode) }   # else setup asks: own account / create demo / skip
& $uv @args2
if ($LASTEXITCODE -eq 3) { Say "Done. Start TradeX from the Start menu, or run $Dir\TradeX.cmd (MetaTrader 5 waits for your demo account)"; exit 0 }
if ($LASTEXITCODE -ne 0) { Write-Host "Setup stopped. Fix the line marked with a cross, then run this installer again (it continues where it stopped)." -ForegroundColor Yellow; exit 1 }
Say "Done. Start TradeX from the Start menu, or run $Dir\TradeX.cmd"
