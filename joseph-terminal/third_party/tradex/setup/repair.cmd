@echo off
rem Re-run TradeX setup (repair / continue where it stopped). Keeps every cache and temp file inside this folder.
rem   setup\repair.cmd            all steps
rem   setup\repair.cmd --list     show which steps are done
cd /d "%~dp0.."
set "TX=%CD%"
set "UV_CACHE_DIR=%TX%\runtime\uv-cache"
set "UV_PYTHON_INSTALL_DIR=%TX%\runtime\python"
set "UV_TOOL_DIR=%TX%\runtime\uv-tools"
set "TEMP=%TX%\runtime\tmp"
set "TMP=%TX%\runtime\tmp"
if "%~1"=="" (set "ARGS=--all") else (set "ARGS=%*")
"%TX%\runtime\uv\uv.exe" run --no-project --python 3.12 python "%TX%\setup\tradex_setup.py" %ARGS%
rem Double-click (no arguments): keep the window open so the person can read the result. Agents pass arguments.
if "%~1"=="" pause
