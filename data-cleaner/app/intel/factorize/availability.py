"""P3-1 防前视基础 + P3-2 版本化落库

核心不变式（P3-1）：
    available_at = max(published_at, ingested_at)
回测在 T 日只能看见 available_at <= T 的因子值。文档发布早于入库时
（历史补录 / 导入场景）以入库时间为准，避免回测用到当时尚不可得的信息。

版本化不覆盖（P3-2 验收）：
    唯一键 (symbol, trade_date, factor_code, factor_version, prompt_version)。
    LLM 重跑产生新 prompt_version 时插入新行，旧版本行的 available_at 与
    可见性保持不变 —— 回测读旧版本时看到的结果不受重跑影响。
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Sequence

from sqlalchemy import text

from app.intel.core.logging import get_logger
from app.intel.store import get_engine

logger = get_logger(__name__)


def _ensure_dt(v):
    """容忍 str / datetime 两种输入"""
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v))


def compute_available_at(published_at, ingested_at):
    """因子对回测可见的最早时刻 = max(published_at, ingested_at)。

    任一为 None → 取另一个；两者均 None → 返回 None（该行不可用于回测）。
    """
    pub = _ensure_dt(published_at)
    ing = _ensure_dt(ingested_at)
    if pub is None and ing is None:
        return None
    if pub is None:
        return ing
    if ing is None:
        return pub
    return max(pub, ing)


def is_visible_at(available_at, as_of) -> bool:
    """因子值在 as_of 时刻是否对回测可见"""
    av = _ensure_dt(available_at)
    if av is None:
        return False
    return av <= _ensure_dt(as_of)


def filter_visible(rows: Iterable[dict], as_of) -> list[dict]:
    """按 available_at <= as_of 裁剪因子行，防前视。"""
    return [r for r in rows if is_visible_at(r.get("available_at"), as_of)]


def build_factor_rows(
    symbol: str,
    trade_date,
    factor_code: str,
    value,
    published_at,
    ingested_at,
    factor_version: str = "v1",
    prompt_version: str = "v2",
) -> dict | None:
    """构造一行因子记录（含 available_at）。

    available_at 为 None（published_at 与 ingested_at 均缺失）时返回 None，
    由调用方跳过 —— 宁可缺值，不可引入前视。
    """
    av = compute_available_at(published_at, ingested_at)
    if av is None:
        logger.warning(
            f"skip factor row: no available_at "
            f"(symbol={symbol} date={trade_date} code={factor_code})"
        )
        return None
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "factor_code": factor_code,
        "value": value,
        "available_at": av,
        "published_at": _ensure_dt(published_at),
        "ingested_at": _ensure_dt(ingested_at),
        "factor_version": factor_version,
        "prompt_version": prompt_version,
    }


def upsert_factor_values(
    rows: Sequence[dict],
    engine=None,
) -> int:
    """版本化写入 intel.factor_values。

    同 (symbol, trade_date, factor_code, factor_version, prompt_version) 覆盖更新，
    不同版本各自成行 —— 保证 LLM 重跑不改写旧版本行的可见性。

    返回写入行数。
    """
    if not rows:
        return 0
    if engine is None:
        engine = get_engine()

    sql = text("""
        INSERT INTO intel.factor_values
            (symbol, trade_date, factor_code, value,
             available_at, published_at, ingested_at,
             factor_version, prompt_version)
        VALUES
            (:symbol, :trade_date, :factor_code, :value,
             :available_at, :published_at, :ingested_at,
             :factor_version, :prompt_version)
        ON CONFLICT (symbol, trade_date, factor_code, factor_version, prompt_version)
        DO UPDATE SET
            value        = EXCLUDED.value,
            available_at = EXCLUDED.available_at,
            published_at = EXCLUDED.published_at,
            ingested_at  = EXCLUDED.ingested_at,
            computed_at  = now()
    """)

    n = 0
    with engine.begin() as c:
        for r in rows:
            if r is None:
                continue
            c.execute(sql, r)
            n += 1
    logger.info(f"upsert {n} factor rows")
    return n


def fetch_visible_factor_values(
    factor_code: str,
    as_of,
    factor_version: str = "v1",
    prompt_version: str | None = None,
    engine=None,
):
    """按防前视规则读取某因子在 as_of 可见的全部值（回测消费侧入口）。

    prompt_version 为 None 时不限制版本（由调用方明确选取版本，避免隐式混版）。
    """
    if engine is None:
        engine = get_engine()

    if prompt_version is None:
        sql = text("""
            SELECT symbol, trade_date, value, available_at, factor_version, prompt_version
            FROM intel.factor_values
            WHERE factor_code = :code
              AND factor_version = :fv
              AND available_at <= CAST(:as_of AS timestamptz)
            ORDER BY trade_date, symbol
        """)
        params = {"code": factor_code, "fv": factor_version, "as_of": as_of}
    else:
        sql = text("""
            SELECT symbol, trade_date, value, available_at, factor_version, prompt_version
            FROM intel.factor_values
            WHERE factor_code = :code
              AND factor_version = :fv
              AND prompt_version = :pv
              AND available_at <= CAST(:as_of AS timestamptz)
            ORDER BY trade_date, symbol
        """)
        params = {
            "code": factor_code,
            "fv": factor_version,
            "pv": prompt_version,
            "as_of": as_of,
        }

    with engine.connect() as c:
        rows = c.execute(sql, params).mappings().all()
    return [dict(r) for r in rows]
