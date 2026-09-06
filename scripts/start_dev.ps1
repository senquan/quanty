# 本地开发一键启动（Windows）
# 后台拉起 backend(:8000) 与 data-cleaner(:8100)，并统一设置 PYTHONUTF8=1。
#
# 为什么必须 PYTHONUTF8=1：
#   Windows 控制台默认 GBK 代码页，当服务日志里出现非 GBK 字符（中文/特殊符号）时，
#   logging 写出会抛 UnicodeEncodeError 并令进程崩溃——data-cleaner 此前就因此掉线。
#   设置 PYTHONUTF8=1 让 Python 以 UTF-8 处理 stdin/stdout/stderr（比 PYTHONIOENCODING
#   更彻底，还覆盖文件系统编码），彻底规避该问题。
#
# 用法：
#   powershell -File scripts/start_dev.ps1
# 停止：任务管理器结束对应 python 进程，或自行 kill 占用 8000/8100 的进程。

$ErrorActionPreference = 'Stop'

# —— 关键：Windows 下统一 UTF-8，避免日志写出 GBK 崩溃 ——
$env:PYTHONUTF8 = '1'

$Root = Resolve-Path (Join-Path $PSScriptRoot '..')
$Be   = Join-Path $Root 'backend'
$Dc   = Join-Path $Root 'data-cleaner'

# backend（:8000，沿用 README 的 `python main.py`）
Start-Process -FilePath (Join-Path $Be '.venv\Scripts\python.exe') `
    -ArgumentList 'main.py' `
    -WorkingDirectory $Be `
    -RedirectStandardOutput (Join-Path $Be 'be.out.log') `
    -RedirectStandardError  (Join-Path $Be 'be.err.log') `
    -WindowStyle Hidden

# data-cleaner（:8100，沿用 README 的 `uvicorn app.main:app --port 8100`）
Start-Process -FilePath (Join-Path $Dc '.venv\Scripts\python.exe') `
    -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8100' `
    -WorkingDirectory $Dc `
    -RedirectStandardOutput (Join-Path $Dc 'data\dc.out.log') `
    -RedirectStandardError  (Join-Path $Dc 'data\dc.err.log') `
    -WindowStyle Hidden

Write-Host "已启动 backend(:8000) 与 data-cleaner(:8100) [PYTHONUTF8=1]"
Write-Host "日志：backend/be.*.log 与 data-cleaner/data/dc.*.log"
