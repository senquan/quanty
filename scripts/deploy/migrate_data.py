#!/usr/bin/env python3
"""QuanT 数据迁移工具：导出 / 校验 / 导入（跨平台，源机可为 Windows）

配套文档：docs/plans/2026-09-10.dc-public-deploy-and-data-migration.md

迁移内容（本地 backend 与 dc 共用一个库、分属两个 schema，故按 schema 拆分）：
  - backend : `public` schema  → pg_dump -n public → 恢复到 quant_db
  - dc      : `factor` schema  → pg_dump -n factor → 恢复到 factor_db
  - 因子 parquet 库            → tar.gz        → 解到 dc 的 /data/factors

用法
----
# 源机：导出（生成 <out>/ 内的 dump、factors.tgz、SHA256SUMS、manifest.json）
python scripts/deploy/migrate_data.py export \
    --pg-url "postgresql://quant_user:***@127.0.0.1:5432/quant" \
    --factor-dir data/factors \
    --out ./migrate

# 任意：校验完整性
python scripts/deploy/migrate_data.py verify --dir ./migrate

# 目标机：导入（默认走 docker compose；也可 --mode local）
python scripts/deploy/migrate_data.py import --dir ./migrate --mode docker
python scripts/deploy/migrate_data.py import --dir ./migrate --mode local \
    --backend-url "postgresql://quant_user:***@127.0.0.1:5432/quant_db" \
    --factor-url  "postgresql://quant_user:***@127.0.0.1:5433/factor_db" \
    --factor-dir  /opt/quant/data/factors

# 任何子命令都可加 --dry-run 只打印将执行的命令
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path

# Windows 控制台默认 GBK，中文输出会抛 UnicodeEncodeError 直接崩（本仓库已知问题）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass

BACKEND_SCHEMA = "public"
DC_SCHEMA = "factor"
DUMP_BACKEND = "backend_public.dump"
DUMP_DC = "dc_factor.dump"
PARQUET_TGZ = "factors.tgz"
SUMS = "SHA256SUMS"
MANIFEST = "manifest.json"

# 迁移后需要断言的关键表（行数必须与 manifest 一致）
KEY_TABLES = {
    BACKEND_SCHEMA: ["users", "menus", "strategies", "cleaner_services"],
    DC_SCHEMA: ["raw_bars", "daily_basic", "finance_reports"],
}


# ---------------- 基础工具 ----------------


def log(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: list[str], *, dry_run: bool = False, check: bool = True, **kw) -> subprocess.CompletedProcess:
    """执行命令；dry_run 时只打印。"""
    printable = " ".join(cmd)
    log(f"  $ {printable}")
    if dry_run:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    cp = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
    if check and cp.returncode != 0:
        raise RuntimeError(f"命令失败({cp.returncode}): {printable}\n{cp.stderr.strip()[:800]}")
    return cp


def need(tool: str) -> None:
    if shutil.which(tool) is None:
        raise SystemExit(f"未找到 `{tool}`，请先安装 PostgreSQL 客户端工具并加入 PATH")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def human(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n}"


# ---------------- 导出 ----------------


def psql_query(pg_url: str, sql: str, *, dry_run: bool = False) -> list[list[str]]:
    # ⚠️ Windows 的 psql 不做选项置换：dbname/URL 必须放在**最后**，
    # 否则后续 `-c` 会被静默当作"多余参数"忽略（rc=0 但无输出）。
    # 另：`-c` 需与参数分开写（`-tAc <sql>` 在部分平台不吞参数）。
    cp = run(
        ["psql", "-X", "-tA", "-F", "|", "-c", sql, pg_url],
        dry_run=dry_run,
        check=not dry_run,
    )
    if dry_run:
        return []
    return [ln.split("|") for ln in cp.stdout.strip().splitlines() if ln.strip()]


def table_counts(pg_url: str, schema: str, *, dry_run: bool = False) -> dict[str, int]:
    """一次 UNION 查询取该 schema 全部基表的行数。"""
    rows = psql_query(
        pg_url,
        "SELECT string_agg("
        "  format('SELECT %L AS t, count(*) AS n FROM %I.%I', table_name, table_schema, table_name),"
        "  ' UNION ALL ')"
        f" FROM information_schema.tables WHERE table_schema='{schema}' AND table_type='BASE TABLE'",
        dry_run=dry_run,
    )
    if not rows:
        return {}
    union_sql = rows[0][0]
    if not union_sql:
        return {}
    out: dict[str, int] = {}
    for r in psql_query(pg_url, union_sql, dry_run=dry_run):
        if len(r) == 2:
            out[r[0]] = int(r[1])
    return out


def cmd_export(args: argparse.Namespace) -> int:
    need("pg_dump")
    need("psql")
    out = Path(args.out).resolve()
    factor_dir = Path(args.factor_dir).resolve()
    if not factor_dir.is_dir():
        raise SystemExit(f"因子目录不存在: {factor_dir}")
    if not args.dry_run:
        out.mkdir(parents=True, exist_ok=True)

    log(f"[1/5] 记录源库元信息 …")
    db_name = psql_query(args.pg_url, "SELECT current_database()", dry_run=args.dry_run)
    tz = psql_query(args.pg_url, "SHOW timezone", dry_run=args.dry_run)
    ver = psql_query(args.pg_url, "SHOW server_version", dry_run=args.dry_run)
    db_name = db_name[0][0] if db_name else "?"
    tz = tz[0][0] if tz else "?"
    ver = ver[0][0] if ver else "?"
    log(f"      db={db_name} timezone={tz} server={ver}")
    if tz != "Asia/Shanghai":
        log(f"      ⚠️ 源库时区为 {tz}（非 Asia/Shanghai）——目标库务必保持一致，否则日期会偏移")

    log(f"[2/5] 导出两个 schema 的 dump …")
    run(["pg_dump", "-Fc", "-n", BACKEND_SCHEMA, "-f", str(out / DUMP_BACKEND), args.pg_url], dry_run=args.dry_run)
    run(["pg_dump", "-Fc", "-n", DC_SCHEMA, "-f", str(out / DUMP_DC), args.pg_url], dry_run=args.dry_run)

    log(f"[3/5] 打包因子 parquet（{factor_dir.name}/）…")
    tgz = out / PARQUET_TGZ
    n_files = sum(len(fs) for _, _, fs in os.walk(factor_dir))
    if not args.dry_run:
        with tarfile.open(tgz, "w:gz") as tf:
            tf.add(factor_dir, arcname=factor_dir.name)
    log(f"      文件数={n_files:,}")

    log(f"[4/5] 采集行数清单（用于目标机断言）…")
    counts = {
        BACKEND_SCHEMA: table_counts(args.pg_url, BACKEND_SCHEMA, dry_run=args.dry_run),
        DC_SCHEMA: table_counts(args.pg_url, DC_SCHEMA, dry_run=args.dry_run),
    }

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_db": db_name,
        "timezone": tz,
        "pg_server_version": ver,
        "schemas": {BACKEND_SCHEMA: "public(backend)", DC_SCHEMA: "factor(data-cleaner)"},
        "table_counts": counts,
        "factor_files": n_files,
        "factor_dir_name": factor_dir.name,
    }

    log(f"[5/5] 生成 SHA256SUMS 与 manifest …")
    artifacts = [DUMP_BACKEND, DUMP_DC, PARQUET_TGZ]
    lines = []
    for name in artifacts:
        p = out / name
        if args.dry_run:
            continue
        digest = sha256_of(p)
        size = p.stat().st_size
        lines.append(f"{digest}  {name}")
        log(f"      {name:<22} {human(size):>12}  {digest[:16]}…")
    if not args.dry_run:
        (out / SUMS).write_text("\n".join(lines) + "\n", encoding="utf-8")
        manifest["artifacts"] = {
            n: {"sha256": sha256_of(out / n), "bytes": (out / n).stat().st_size} for n in artifacts
        }
        (out / MANIFEST).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    log(f"\n✅ 导出完成 → {out}")
    log("   下一步：把整个目录传输到目标机，再执行 verify / import")
    return 0


# ---------------- 校验 ----------------


def parse_sums(path: Path) -> list[tuple[str, str]]:
    items = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        parts = ln.split(None, 1)
        if len(parts) == 2:
            items.append((parts[0], parts[1].lstrip("*")))
    return items


def verify_dir(d: Path) -> bool:
    sums = d / SUMS
    if not sums.exists():
        log(f"❌ 缺少 {SUMS}")
        return False
    ok = True
    for expect, name in parse_sums(sums):
        p = d / name
        if not p.exists():
            log(f"  ❌ 缺失 {name}")
            ok = False
            continue
        actual = sha256_of(p)
        flag = "✅" if actual == expect else "❌"
        if actual != expect:
            ok = False
        log(f"  {flag} {name:<22} {human(p.stat().st_size):>12}")
    return ok


def cmd_verify(args: argparse.Namespace) -> int:
    d = Path(args.dir).resolve()
    log(f"[verify] {d}")
    return 0 if verify_dir(d) else 1


# ---------------- 导入 ----------------


def compose(args: argparse.Namespace) -> list[str]:
    base = ["docker", "compose"]
    if args.compose_file:
        base += ["-f", args.compose_file]
    return base


def psql_assert(
    args: argparse.Namespace,
    *,
    table: str,
    expect: int,
    schema: str,
) -> bool:
    """在目标端取行数并断言。docker 模式走容器内 psql，local 模式走宿主 psql。"""
    sql = f"SELECT count(*) FROM {schema}.{table}"
    if args.mode == "docker":
        svc = "postgres" if schema == BACKEND_SCHEMA else "postgres-factor"
        db = "quant_db" if schema == BACKEND_SCHEMA else "factor_db"
        cp = run(compose(args) + ["exec", "-T", svc, "psql", "-U", args.db_user, "-d", db, "-tA", "-c", sql],
                 dry_run=args.dry_run, check=False)
    else:
        url = args.backend_url if schema == BACKEND_SCHEMA else args.factor_url
        # 同上：URL 放最后
        cp = run(["psql", "-X", "-tA", "-c", sql, url], dry_run=args.dry_run, check=False)
    if args.dry_run:
        return True
    try:
        actual = int((cp.stdout or "").strip())
    except ValueError:
        log(f"  ❌ {schema}.{table}: 无法取得行数（{cp.stderr.strip()[:120]}）")
        return False
    if actual != expect:
        log(f"  ❌ {schema}.{table}: 实际 {actual:,} ≠ 期望 {expect:,}")
        return False
    log(f"  ✅ {schema}.{table}: {actual:,}")
    return True


def cmd_import(args: argparse.Namespace) -> int:
    d = Path(args.dir).resolve()
    manifest_path = d / MANIFEST
    if not manifest_path.exists():
        raise SystemExit(f"缺少 {MANIFEST}（请先在该目录执行 export）")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    log("[0/4] 校验产物完整性 …")
    if not args.dry_run and not verify_dir(d):
        if not args.force:
            raise SystemExit("产物校验失败；确认要用 --force 继续？")
        log("  ⚠️ 校验失败但 --force，继续")

    if args.mode == "docker":
        need("docker")
    else:
        need("pg_restore")

    log("[1/4] 恢复 backend 库（public → quant_db）…")
    if args.mode == "docker":
        run(compose(args) + ["cp", str(d / DUMP_BACKEND), "postgres:/tmp/migrate_backend.dump"], dry_run=args.dry_run)
        run(compose(args) + ["exec", "-T", "postgres", "pg_restore", "-U", args.db_user, "-d", "quant_db",
                             "--no-owner", "--no-privileges", "--clean", "--if-exists",
                             "/tmp/migrate_backend.dump"], dry_run=args.dry_run, check=False)
    else:
        run(["pg_restore", "--no-owner", "--no-privileges", "--clean", "--if-exists",
             "-d", args.backend_url, str(d / DUMP_BACKEND)], dry_run=args.dry_run, check=False)

    log("[2/4] 恢复 dc 库（factor → factor_db）…")
    if args.mode == "docker":
        run(compose(args) + ["cp", str(d / DUMP_DC), "postgres-factor:/tmp/migrate_dc.dump"], dry_run=args.dry_run)
        run(compose(args) + ["exec", "-T", "postgres-factor", "pg_restore", "-U", args.db_user, "-d", "factor_db",
                             "--no-owner", "--no-privileges", "--clean", "--if-exists", f"-j{args.jobs}",
                             "/tmp/migrate_dc.dump"], dry_run=args.dry_run, check=False)
    else:
        run(["pg_restore", "--no-owner", "--no-privileges", "--clean", "--if-exists",
             f"-j{args.jobs}", "-d", args.factor_url, str(d / DUMP_DC)], dry_run=args.dry_run, check=False)

    log("[3/4] 恢复因子 parquet …")
    unpacked = d / "unpacked"
    if not args.dry_run and not unpacked.exists():
        unpacked.mkdir(parents=True, exist_ok=True)
        with tarfile.open(d / PARQUET_TGZ) as tf:
            tf.extractall(unpacked)
    if args.mode == "docker":
        src = f"{unpacked / manifest['factor_dir_name']}{os.sep}."
        run(compose(args) + ["cp", src, "data-cleaner:/data/factors/"], dry_run=args.dry_run)
    else:
        dest = Path(args.factor_dir).resolve()
        if args.dry_run:
            log(f"  $ 复制 {unpacked / manifest['factor_dir_name']} → {dest}")
        else:
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copytree(unpacked / manifest["factor_dir_name"], dest, dirs_exist_ok=True)

    log("[4/4] 行数断言 …")
    ok = True
    for schema, tables in KEY_TABLES.items():
        for t in tables:
            expect = manifest["table_counts"].get(schema, {}).get(t)
            if expect is None:
                log(f"  ⏭ {schema}.{t}: manifest 无记录，跳过")
                continue
            ok = psql_assert(args, table=t, expect=expect, schema=schema) and ok

    if args.dry_run:
        log("\n(dry-run) 未真正执行")
        return 0
    if not ok:
        log("\n❌ 导入完成但有断言失败，请检查上面 ❌ 行")
        return 1
    log(f"\n✅ 导入完成；源库时区={manifest['timezone']}（目标库务必一致）")
    log("   接着按文档 §6 步骤 8/9 启动 dc/backend 并验证 WS 与覆盖度")
    return 0


# ---------------- CLI ----------------


def main() -> int:
    p = argparse.ArgumentParser(description="QuanT 数据迁移工具（export / verify / import）")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("export", help="源机：导出 dump + parquet + 校验清单")
    e.add_argument("--pg-url", required=True, help="源库连接串（含 public 与 factor 两个 schema）")
    e.add_argument("--factor-dir", default="data/factors", help="因子 parquet 目录")
    e.add_argument("--out", default="./migrate", help="导出目录")
    e.add_argument("--dry-run", action="store_true")
    e.set_defaults(func=cmd_export)

    v = sub.add_parser("verify", help="校验产物 sha256")
    v.add_argument("--dir", default="./migrate")
    v.set_defaults(func=cmd_verify)

    i = sub.add_parser("import", help="目标机：恢复两个库与 parquet，并断言行数")
    i.add_argument("--dir", default="./migrate")
    i.add_argument("--mode", choices=["docker", "local"], default="docker")
    i.add_argument("--compose-file", default=None, help="默认 ./docker-compose.yml")
    i.add_argument("--db-user", default="quant_user")
    i.add_argument("--backend-url", help="mode=local 时的 backend 目标库连接串")
    i.add_argument("--factor-url", help="mode=local 时的 dc 目标库连接串")
    i.add_argument("--factor-dir", default="./data/factors", help="mode=local 时 parquet 落地目录")
    i.add_argument("--jobs", type=int, default=4, help="pg_restore 并行度（dc 大库）")
    i.add_argument("--force", action="store_true", help="校验失败也继续")
    i.add_argument("--dry-run", action="store_true")
    i.set_defaults(func=cmd_import)

    args = p.parse_args()
    if args.cmd == "import" and args.mode == "local" and not (args.backend_url and args.factor_url):
        raise SystemExit("mode=local 需同时提供 --backend-url 与 --factor-url")
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
