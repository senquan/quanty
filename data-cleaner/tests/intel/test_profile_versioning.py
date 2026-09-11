"""防回归（D-9）：画像必须按 as_of 版本化，**同版本不得覆盖历史**。

背景（2026-09-10 审计 D-9）：
    intel.author_profiles 的唯一键曾是 (profile_key, profile_type, profile_version)，
    profile_version 恒为常量 'v1' ⇒ 每日重算永远命中同一行、原地覆盖。
    后果：历史截面不可复现 —— 09-07/09-08 的因子行里 INTL_AUTHOR_CONVICTION
    会随每次重算改变（实测 0.9612 → 0.5683 → 0.8881）。

修法：唯一键并入 as_of（知识截止日）：
    (profile_key, profile_type, profile_version, as_of)
    ⇒ 同日重跑幂等覆盖，跨天重跑新增历史行。

本文件锁死三件事（都是"改回去也不会报错"的静默陷阱）：
1. DDL：唯一索引含 as_of 且 as_of 为 NOT NULL；
2. DML：build_profiles 的 ON CONFLICT 目标含 as_of；
3. 读取：所有"取最新画像"的 SQL 按 as_of 判定（而不是 computed_at）。

纯静态断言 + 若库可达则做一次真实的幂等/新增验证。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PKG_ROOT = Path(__file__).resolve().parents[2]
MIG_018 = PKG_ROOT / "migrations" / "018_intel_profiles_asof.sql"
PROFILE_PY = PKG_ROOT / "app" / "intel" / "aggregate" / "profile.py"
FACTORS_PY = PKG_ROOT / "app" / "intel" / "factorize" / "factors.py"
STYLE_PY = PKG_ROOT / "app" / "intel" / "aggregate" / "style_summary.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------- DDL

def test_migration_018_exists():
    assert MIG_018.is_file(), f"缺少 D-9 版本化迁移: {MIG_018}"


def test_unique_index_includes_as_of():
    """唯一索引必须含 as_of —— 否则版本化失效（回到原地覆盖）。"""
    sql = _read(MIG_018)
    m = re.search(
        r"CREATE UNIQUE INDEX IF NOT EXISTS \w+\s+ON intel\.author_profiles\(([^)]*)\)",
        sql,
        re.IGNORECASE,
    )
    assert m, "018 里没找到 author_profiles 的唯一索引定义"
    cols = [c.strip() for c in m.group(1).split(",")]
    assert "as_of" in cols, f"唯一索引不含 as_of: {cols}"
    # 旧的 4 列键必须保留（保持向后兼容的版本维度）
    for c in ("profile_key", "profile_type", "profile_version"):
        assert c in cols, f"唯一索引缺少 {c}: {cols}"


def test_drops_old_index():
    """必须显式 drop 掉不含 as_of 的旧唯一索引，否则新索引建不起来/形同虚设。"""
    sql = _read(MIG_018)
    assert re.search(
        r"DROP INDEX IF EXISTS intel\.idx_intel_profiles_key_ver\b", sql
    ), "018 未 drop 旧唯一索引 idx_intel_profiles_key_ver"


def test_as_of_is_not_null():
    """as_of 可空 ⇒ NULL 不参与唯一冲突判定 ⇒ 版本化静默失效。必须 NOT NULL。"""
    sql = _read(MIG_018)
    assert re.search(
        r"ALTER COLUMN as_of SET NOT NULL", sql, re.IGNORECASE
    ), "018 未把 as_of 置为 NOT NULL"


# ---------------------------------------------------------------- DML

def test_build_profiles_conflict_target_includes_as_of():
    """build_profiles 的 ON CONFLICT 目标必须含 as_of。"""
    src = _read(PROFILE_PY)
    m = re.search(r"ON CONFLICT \(([^)]*)\)", src)
    assert m, "profile.py 里没找到 ON CONFLICT"
    cols = [c.strip() for c in m.group(1).split(",")]
    assert "as_of" in cols, (
        f"build_profiles 的 ON CONFLICT 目标不含 as_of: {cols} —— "
        f"这样跨天重算会原地覆盖，D-9 复发"
    )


def test_build_profiles_accepts_as_of_param():
    """build_profiles 必须接受 as_of 参数（默认今天），否则无法指定历史版本。"""
    src = _read(PROFILE_PY)
    assert re.search(
        r"def build_profiles\([^)]*as_of", src, re.DOTALL
    ), "build_profiles 签名里没有 as_of 参数"


def test_insert_column_list_includes_as_of():
    """INSERT 的列清单与 VALUES 都要含 as_of —— 漏一处就会写 NULL 或列数不匹配。"""
    src = _read(PROFILE_PY)
    assert "profile_version, as_of" in src.replace("\n", " ").replace(
        "  ", " "
    ) or "as_of," in src, "INSERT 列清单里未见 as_of"


# ---------------------------------------------------------------- 读取口径

def _profile_query_blocks(src: str) -> list[str]:
    """抽出所有「查询 intel.author_profiles 的 SQL 块」（前后各留 600 字符窗口）。

    只对**画像表**的查询做口径断言 —— author_style_summaries 是另一张表，
    它自己的"最新"仍按 computed_at 判定，不归本测试管。
    """
    blocks: list[str] = []
    for m in re.finditer(r"FROM intel\.author_profiles", src):
        # 往前找到 SQL 起始（最近的三引号或 SELECT），往后到三引号/分号结束
        start = max(0, m.start() - 600)
        end = min(len(src), m.end() + 600)
        blocks.append(src[start:end])
    return blocks


def test_loaders_use_as_of_not_computed_at():
    """"取最新画像"必须按 as_of 判定。

    computed_at 只是写入时间：同日重跑会刷新它，跨天新增时旧行不动 ——
    用它做版本判定会在"同日重跑"后指向错误的行。
    """
    for path in (FACTORS_PY, STYLE_PY):
        src = _read(path)
        assert "author_profiles" in src, f"{path.name} 未读取 author_profiles"
        blocks = _profile_query_blocks(src)
        assert blocks, f"{path.name} 里未定位到 author_profiles 查询块"
        for blk in blocks:
            assert "max(computed_at)" not in blk, (
                f"{path.name} 的 author_profiles 查询仍用 max(computed_at) 判定"
                f"最新画像 —— D-9 要求改用 max(as_of)"
            )


def test_factors_uses_select_distinct_on_as_of():
    """factors.load_profile_index 用 DISTINCT ON 取最新 as_of。"""
    src = _read(FACTORS_PY)
    assert "DISTINCT ON (profile_key)" in src, "缺少 DISTINCT ON (profile_key)"
    assert re.search(
        r"ORDER BY profile_key, as_of DESC", src
    ), "未按 as_of DESC 取最新"


def test_backend_route_uses_as_of():
    """backend /profiles 路由也必须按 as_of 取最新（跨仓库口径要一致）。"""
    backend_intel = (
        PKG_ROOT.parent / "backend" / "app" / "api" / "api_v1" / "endpoints" / "intel.py"
    )
    if not backend_intel.is_file():
        pytest.skip(f"backend 路由不存在: {backend_intel}")
    src = _read(backend_intel)
    assert "max(as_of)" in src, "backend /profiles 未按 max(as_of) 取最新画像"
    assert "max(computed_at)" not in src, (
        "backend /profiles 仍用 max(computed_at) —— 与 dc 口径不一致"
    )


# ---------------------------------------------------------------- 真实库验证（可选）

def test_real_db_versioning_idempotent_and_append(tmp_path):
    """真机验证：同日重跑幂等、跨天重跑新增、旧截面不被改写。

    库不可达时 skip（CI 无 PG 环境）。
    """
    import sys

    sys.path.insert(0, str(PKG_ROOT))
    try:
        from sqlalchemy import text

        from app.intel.aggregate.profile import build_profiles
        from app.intel.store import get_engine
    except Exception as e:  # pragma: no cover
        pytest.skip(f"依赖不可用: {e}")

    try:
        e = get_engine()
        with e.connect() as c:
            c.execute(text("SELECT 1"))
            cols = {
                r[0]
                for r in c.execute(
                    text(
                        """
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema='intel' AND table_name='author_profiles'
                          AND column_name='as_of'
                    """
                    )
                )
            }
    except Exception as e:  # pragma: no cover
        pytest.skip(f"数据库不可达: {e}")

    if not cols:
        pytest.skip("as_of 列不存在（未跑 018 迁移）")

    from datetime import date

    def count() -> int:
        with e.connect() as c:
            return c.execute(
                text("SELECT count(*) FROM intel.author_profiles")
            ).scalar()

    base = count()

    # 同日重跑（用库里已存在的 as_of，保证命中已有行）
    with e.connect() as c:
        existing = c.execute(
            text("SELECT max(as_of) FROM intel.author_profiles")
        ).scalar()
    if existing is None:
        pytest.skip("表内无数据，无法验证幂等")

    build_profiles(prompt_version="v2", profile_version="v1", as_of=existing, engine=e)
    assert count() == base, "同日重跑不应改变行数（应幂等覆盖）"

    # 跨天重跑（挑一个不存在的 as_of，写在 2000-01-01 这种明显是测试的日子）
    probe = date(2000, 1, 1)
    with e.begin() as c:
        c.execute(
            text("DELETE FROM intel.author_profiles WHERE as_of = :d"),
            {"d": probe},
        )
    build_profiles(prompt_version="v2", profile_version="v1", as_of=probe, engine=e)
    after = count()
    assert after > base, "跨 as_of 重跑应新增历史行"

    # 清理测试行
    with e.begin() as c:
        c.execute(
            text("DELETE FROM intel.author_profiles WHERE as_of = :d"),
            {"d": probe},
        )
    assert count() == base, "清理后应回到基线"
