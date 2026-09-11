"""防回归：迁移文件不得含"意外的 bind parameter"

背景（2026-09-08 线上踩坑）：
    migrations/013_intel_profiles.sql 的注释里写了 `-- [{"count":8}]`，
    经 SQLAlchemy `text()` 执行时 `:8` 被解析成**编号 bind parameter**，报：
        StatementError: A value is required for bind parameter '8'
    因为冒号藏在 DDL 行尾注释里，极难察觉；而 DDL 本身没有任何参数，
    报错信息又只给编号，指向性很差。

防线（两层）：
    1. 迁移文件注释里禁止 "冒号 + 紧跟字母/数字" → 本测试静态扫描锁死；
    2. 执行侧一律用 `app.intel.store.run_sql_file()`（DBAPI 原生执行，
       完全绕开 bind 解析），不再用 `engine.execute(text(sql))`。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _sql_files() -> list[Path]:
    if not MIGRATIONS_DIR.is_dir():
        pytest.skip(f"migrations 目录不存在: {MIGRATIONS_DIR}")
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def test_migrations_dir_has_files():
    assert _sql_files(), f"未找到迁移文件: {MIGRATIONS_DIR}"


def test_no_unintended_bind_params_in_migrations():
    """每个迁移文件经 text() 解析后都不应产生 bind parameter。

    迁移是纯 DDL/静态 SQL，不含运行时参数；一旦出现 bind parameter，
    几乎必然是注释里的 "key:value" 被误解析（如 {"count":8}）。
    """
    offenders: dict[str, list[str]] = {}
    for path in _sql_files():
        sql = path.read_text(encoding="utf-8")
        try:
            stmt = text(sql)
        except Exception as exc:  # text() 自身解析失败也算问题
            offenders[path.name] = [f"text() 解析异常: {exc!r}"]
            continue
        params = sorted(getattr(stmt, "_bindparams", {}) or {})
        if params:
            offenders[path.name] = params

    assert not offenders, (
        "以下迁移文件含意外 bind parameter（多半是注释里的 '键:值' 被误解析，"
        "请改用 '键 = 值' 或空格分隔）：\n"
        + "\n".join(f"  - {name}: {params}" for name, params in offenders.items())
    )


def test_run_sql_file_exists_and_uses_raw_connection():
    """执行侧防线：store 必须提供 run_sql_file（原生执行）。"""
    from app.intel import store

    assert callable(getattr(store, "run_sql_file", None)), (
        "app.intel.store.run_sql_file 缺失 —— 迁移应经它原生执行，"
        "避免 text() 解析注释里的冒号"
    )
    src = Path(store.__file__).read_text(encoding="utf-8")
    assert "raw_connection" in src, "run_sql_file 必须使用 raw_connection 原生执行"
