# QuanT data-cleaner 本机启动脚本（**带日志轮转**）
#
# 背景：此前用重定向 `>` 覆盖写单一文件，服务一重启日志就没了——
# 2026-09-11 排查 WS 长连接静默失效时，正因日志被覆盖而**取不到首次触发的证据**
# （见 docs/memo/2026-09-11.ws-reconnect-reliability.md §6）。
# 故改为：每次启动各写一份带时间戳的日志，并按保留份数自动轮转。
#
# 用法：
#   powershell -File data-cleaner\_run_real_dc.ps1
# 或调整保留份数：
#   powershell -File data-cleaner\_run_real_dc.ps1 -Keep 20

param(
    [int]$Keep = 10
)

$ErrorActionPreference = 'Continue'

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $Root 'logs'
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

# 轮转：只保留最近 $Keep 份（按创建时间倒序），其余删除
Get-ChildItem -Path $LogDir -Filter 'dc-*.log' -File -ErrorAction SilentlyContinue |
    Sort-Object CreationTime -Descending |
    Select-Object -Skip $Keep |
    Remove-Item -Force -ErrorAction SilentlyContinue

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$out = Join-Path $LogDir "dc-$stamp.out.log"
$err = Join-Path $LogDir "dc-$stamp.err.log"

# Windows 控制台默认 GBK，日志含非 GBK 字符会抛 UnicodeEncodeError 令进程退出
$env:PYTHONUTF8 = '1'

Set-Location $Root
$py = Join-Path $Root '.venv\Scripts\python.exe'
Write-Host "dc 启动中（保留最近 $Keep 份日志）"
Write-Host "  stdout -> $out"
Write-Host "  stderr -> $err"

& $py -m uvicorn app.main:app --host 0.0.0.0 --port 8100 > $out 2> $err
