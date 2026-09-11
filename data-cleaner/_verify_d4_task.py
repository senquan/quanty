"""D-4 验收：19:30 定时任务是否在【运行中的进程】生效（只读 + 可选干跑）。

为什么需要它
------------
静态 import 能拿到 `_daily_intel_build_job` **不代表**运行中的 dc 跑的是这份代码：
进程若早于代码 mtime 启动，加载的仍是旧的空占位（`_daily_intel_build_placeholder`）。
2026-09-10 的 D-4 就是卡在这个"未确认"上。

本脚本三层取证，逐层加强
------------------------
L1 代码层：`register_jobs()` 后 job 的 func_ref 是否为真实现（可直接 import 验证）。
L2 进程层：运行中 dc 的**启动时刻** vs `app/intel/tasks.py` 的 **mtime**
          —— 进程启动必须晚于代码修改，否则加载的是旧代码。
L3 行为层：dc 日志里的 `Added job "_daily_intel_build_job"`（**进程内真实注册行为**，
          比 L1 更强）+ 调度器 uptime；再可选干跑一轮编排。

⚠️ 时区坑（本脚本封装掉）
------------------------
- Windows `GetProcessTimes` 返回的 FILETIME 是 **UTC**，直接与本地 mtime 比会差 8 小时。
- `/qos` 的 `uptime_seconds` 是**应用级**计数（可能小于进程存活时长），反推启动时刻更准。
- dc 日志 ts 是 UTC（`+00:00`），APScheduler 的 `next run at` 是 CST（`+08:00`）——
  同一行日志两种时区，肉眼比对极易错。

用法
----
    # 只读取证（默认）
    ./.venv/Scripts/python.exe _verify_d4_task.py

    # 加跑一轮编排（跳过花钱的抽取），验证整条链路真能跑通
    ./.venv/Scripts/python.exe _verify_d4_task.py --dry-run-pipeline

    # 指定 dc 的 /qos 地址与日志路径
    ./.venv/Scripts/python.exe _verify_d4_task.py --qos http://127.0.0.1:8100 \
        --log data/_dc_run.out
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import datetime as dt
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

# ⚠️ 中文 Windows 的两种编码，别混：
#   - 系统命令（netstat 等）：GBK（cp936）
#   - 我们自己起的 python 子进程：stdout 是 UTF-8（Python 默认），
#     用 GBK 解会把「抽取[skipped]」变成乱码 → 断言匹配不上（踩过一次）。
_SYS_ENC = "gbk" if sys.platform == "win32" else "utf-8"


def _run_text(cmd: list[str], *, enc: str | None = None, **kw) -> subprocess.CompletedProcess:
    """跑命令并解码 stdout/stderr。enc 默认取系统编码（系统命令用）。"""
    r = subprocess.run(cmd, capture_output=True, **kw)
    charset = enc or _SYS_ENC
    for attr in ("stdout", "stderr"):
        raw = getattr(r, attr)
        if isinstance(raw, bytes):
            setattr(r, attr, raw.decode(charset, errors="replace"))
    return r

HERE = Path(__file__).resolve().parent
TASKS_PY = HERE / "app" / "intel" / "tasks.py"

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
JOB_ID = "intel_daily_build"
REAL_FUNC = "_daily_intel_build_job"
PLACEHOLDER_FUNC = "_daily_intel_build_placeholder"

OK, BAD, WARN = "[OK]", "[!!]", "[?]"


def _cst(ts: dt.datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------- L1

def check_code_layer() -> tuple[bool, str]:
    """换一个新进程 register_jobs()，看 job 绑的是哪个函数。

    注意：这只证明**当前磁盘上的代码**正确，不证明运行中的进程。
    """
    code = (
        "import app.tasks.scheduler as S; S.register_jobs();"
        f"j=S.scheduler.get_job('{JOB_ID}');"
        "print(j.func_ref if j else 'NOT_REGISTERED');"
        "print(j.trigger if j else '-')"
    )
    r = _run_text(
        [sys.executable, "-c", code],
        enc="utf-8",                      # python 子进程 stdout 是 UTF-8
        cwd=str(HERE), timeout=120,
        env={**__import__("os").environ, "INTEL_ENABLED": "true"},
    )
    out = [ln.strip() for ln in (r.stdout or "").strip().splitlines() if ln.strip()]
    if not out:
        return False, f"子进程无输出；stderr={r.stderr.strip()[:300]}"
    func_ref, trigger = out[0], (out[1] if len(out) > 1 else "-")
    if func_ref.endswith(":" + REAL_FUNC):
        return True, f"func_ref={func_ref}  trigger={trigger}"
    if PLACEHOLDER_FUNC in func_ref:
        return False, f"仍是空占位！func_ref={func_ref}"
    return False, f"意外绑定：func_ref={func_ref}"


# --------------------------------------------------------------------- L2

def _proc_start_utc(pid: int) -> dt.datetime | None:
    """Windows: 取进程创建时刻（返回 UTC 的 naive datetime）。"""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        c, e, k, u = (wintypes.FILETIME() for _ in range(4))
        if not k32.GetProcessTimes(
            h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)
        ):
            return None
        v = (c.dwHighDateTime << 32) | c.dwLowDateTime
        return dt.datetime(1601, 1, 1) + dt.timedelta(microseconds=v // 10)
    finally:
        k32.CloseHandle(h)


def _find_dc_pid(port: int) -> int | None:
    """从 netstat 找监听该端口的 PID。"""
    r = _run_text(["netstat", "-ano"])
    for ln in (r.stdout or "").splitlines():
        parts = ln.split()
        if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[3] == "LISTENING":
            try:
                return int(parts[4])
            except ValueError:
                continue
    return None


def check_process_layer(port: int, qos_url: str) -> tuple[bool, str]:
    """进程启动时刻 vs 代码 mtime —— 进程必须晚于代码。"""
    if not TASKS_PY.exists():
        return False, f"找不到 {TASKS_PY}"
    mtime = dt.datetime.fromtimestamp(TASKS_PY.stat().st_mtime)

    pid = _find_dc_pid(port)
    if pid is None:
        return False, f"端口 {port} 无监听进程（dc 没起？）"

    started_utc = _proc_start_utc(pid)
    if started_utc is None:
        return False, f"PID={pid} 取启动时刻失败（权限？）"
    started = started_utc + dt.timedelta(hours=8)  # UTC -> CST

    # 交叉验证：用 /qos 的 uptime 反推（应用级，可能更晚）
    app_start = None
    try:
        with urllib.request.urlopen(qos_url, timeout=10) as resp:
            q = json.loads(resp.read().decode())
        up = (q.get("system") or {}).get("uptime_seconds")
        if up is not None:
            app_start = dt.datetime.now() - dt.timedelta(seconds=float(up))
    except Exception:
        pass

    ok = started > mtime
    detail = (f"PID={pid} 进程启动={_cst(started)} (CST)  "
              f"代码 mtime={_cst(mtime)}")
    if app_start:
        detail += f"  |  应用 uptime 反推={_cst(app_start)}"
    if not ok:
        detail += "  → 进程早于代码，加载的可能是旧版！"
    return ok, detail


# --------------------------------------------------------------------- L3

def check_log_layer(log_path: Path) -> tuple[bool, str]:
    """在 dc 日志里找进程内真实注册记录（兜底手段，日志可能被重启覆盖）。"""
    if not log_path.exists():
        return False, f"日志不存在：{log_path}"
    text = log_path.read_text(encoding="utf-8", errors="replace")

    added = re.findall(r'Added job \\?"([^"\\]+)\\?" to job store', text)
    intel_registered = "intel 定时任务已注册" in text
    hb = re.findall(r'"ts": "([^"]+)".*?_heartbeat_job.*executed successfully', text)
    last_hb = hb[-1] if hb else None

    if not intel_registered and REAL_FUNC not in added:
        return False, (f"日志中既无 'intel 定时任务已注册' 也无 Added job {REAL_FUNC!r}"
                       "（多半被重启覆盖 → 看 L4 由进程自报，更可靠）")
    detail = f"日志含 {REAL_FUNC!r} 的注册记录"
    if last_hb:
        detail += f"  |  调度器最近心跳={last_hb}"
    return True, detail


def check_running_process(qos_url: str) -> tuple[bool, str]:
    """L4：直接问【运行中的 dc】—— /qos 的 system.scheduler 由进程自报。

    这是本脚本最强的一层：不再靠 mtime / 日志推断，而是运行进程里
    APScheduler 实例的真实 job 列表。
    """
    try:
        with urllib.request.urlopen(qos_url, timeout=10) as resp:
            q = json.loads(resp.read().decode())
    except Exception as e:
        return False, f"无法访问 {qos_url}：{type(e).__name__}: {str(e)[:120]}"

    st = ((q.get("system") or {}).get("scheduler")) or {}
    if not st:
        return False, ("/qos 无 system.scheduler 字段 —— "
                       "运行进程是**旧代码**（该字段为 D-4 新增），需重启 dc")
    if not st.get("enabled") or not st.get("running"):
        return False, f"调度器未运行：{st}"

    jobs = st.get("jobs") or []
    hit = next((j for j in jobs if j.get("id") == JOB_ID), None)
    if hit is None:
        return False, f"运行进程中无 {JOB_ID}（共 {len(jobs)} 个 job）"

    func_ref = hit.get("func_ref") or ""
    good = func_ref.endswith(":" + REAL_FUNC)
    detail = (f"运行进程报 job={JOB_ID}  func_ref={func_ref}  "
              f"next_run={hit.get('next_run')}  (共 {len(jobs)} 个 job)")
    if not good:
        detail += f"  → 不是 {REAL_FUNC}，仍在跑旧实现！"
    return good, detail


def parse_job_cron(trigger: str) -> str:
    """把 func_ref 那行旁边的 trigger 转成人读形式。"""
    m = re.match(r"cron\[(.*)\]", trigger or "")
    return f"cron({m.group(1)})" if m else (trigger or "-")


# --------------------------------------------------------------- 干跑编排

def dry_run_pipeline() -> tuple[bool, str]:
    """跑一轮 run_daily_build(do_extract=False)：画像 + 因子走真库，零 LLM 成本。"""
    code = (
        "from app.intel import daily_build as D;"
        "s = D.run_daily_build(do_extract=False);"
        "print(D.format_summary(s));"
        "print('ERRORS=', s['errors']);"
        "print('COST=', s['cost_cny']);"
        "print('CODES=', s['factor_codes'])"
    )
    r = _run_text(
        [sys.executable, "-c", code],
        enc="utf-8",                      # python 子进程 stdout 是 UTF-8
        cwd=str(HERE), timeout=900,
    )
    lines = [ln.strip() for ln in (r.stdout or "").strip().splitlines() if ln.strip()]
    if not lines:
        return False, f"干跑无输出；stderr={(r.stderr or '').strip()[:400]}"
    summary = lines[0]
    errors = next((ln for ln in lines if ln.startswith("ERRORS=")), "ERRORS=?")
    codes = next((ln for ln in lines if ln.startswith("CODES=")), "CODES=?")
    # 注意 ERRORS 的值是 "[]"（中间有个空格，形如 "ERRORS= []"），别写死 "ERRORS=[]"
    ok = ("[]" in errors.split("=", 1)[-1]
          and "画像[ok]" in summary and "因子[ok]" in summary)
    return ok, f"{summary}\n         {errors}\n         {codes}"


# ------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="D-4 验收：定时任务在运行进程中生效")
    ap.add_argument("--port", type=int, default=8100, help="dc 监听端口（默认 8100）")
    ap.add_argument("--qos", default=None, help="dc /qos 地址（默认由 --port 推导）")
    ap.add_argument("--log", default="data/_dc_run.out", help="dc 标准输出日志")
    ap.add_argument("--dry-run-pipeline", action="store_true",
                    help="额外跑一轮编排（跳过花钱的抽取）验证链路")
    args = ap.parse_args()

    qos_url = args.qos or f"http://127.0.0.1:{args.port}/api/v1/qos"
    log_path = Path(args.log) if Path(args.log).is_absolute() else (HERE / args.log)

    print("=" * 74)
    print("D-4 验收：19:30 intel_daily_build 是否在【运行中的进程】生效")
    print("=" * 74)

    results: list[bool] = []

    print("\nL1 代码层（当前磁盘代码绑定的是哪个函数）")
    ok1, d1 = check_code_layer()
    print(f"  {OK if ok1 else BAD} {d1}")
    results.append(ok1)

    print("\nL2 进程层（运行中的 dc 是否晚于代码启动）")
    ok2, d2 = check_process_layer(args.port, qos_url)
    print(f"  {OK if ok2 else BAD} {d2}")
    results.append(ok2)

    print("\nL3 行为层（dc 日志中的注册记录，可能被重启覆盖）")
    ok3, d3 = check_log_layer(log_path)
    print(f"  {OK if ok3 else WARN} {d3}")
    results.append(ok3)

    print("\nL4 运行进程自报（/qos 的 system.scheduler，最强证据）")
    ok4, d4 = check_running_process(qos_url)
    print(f"  {OK if ok4 else BAD} {d4}")
    results.append(ok4)

    if args.dry_run_pipeline:
        print("\nL5 干跑编排（跳过抽取，画像+因子走真库，零 LLM 成本）")
        ok5, d5 = dry_run_pipeline()
        print(f"  {OK if ok5 else BAD} {d5}")
        results.append(ok5)

    print("\n" + "=" * 74)
    # L3 只是兜底，日志被覆盖不算失败 —— 以 L4 为准
    decisive = results[:2] + results[3:]
    if all(decisive):
        print("结论：intel_daily_build 已在运行中的进程生效，链路可用。")
        if not ok3:
            print("      （L3 日志缺记录属正常：dc 重启会覆盖日志，以 L4 为准）")
        return 0
    print("结论：存在未通过项，见上方 [!!] / [?]。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
