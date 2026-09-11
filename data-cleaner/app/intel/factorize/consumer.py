"""P3-4：INTL_* 因子消费侧（dc 回测 / 选股接入）

设计要点（plan §5 P3-4）：

1. **intel 是可选依赖**：intel 停跑 / 表缺失 / 查询异常时，本模块一律返回空结果
   并记 warning，**绝不抛异常** —— 保证 compose 只跑 market-data 时 dc 既有
   功能完全不受影响（P3 验收第 3 条）。

2. **防前视**：读取必须按 ``available_at <= as_of`` 过滤。同一 ``trade_date`` 内，
   收盘后才入库的提及在当日并不可见（生产侧 `to_trade_date()` 已把它推到下一交易日），
   因此回测调用方**必须**传 ``as_of = 决策时刻``，而不是只按 trade_date 取数。

3. ``as_of`` 传 ``date`` 时按当日 **00:00** 处理（严格口径）：此时可见的是
   ``available_at`` 早于该日开盘的因子值，恰是开盘决策能拿到的信息集。

注意：SQL 注释中不要写 ``键: 值`` ，SQLAlchemy ``text()`` 会把 ``:值`` 解析成
bind parameter（见 docs/memo/2026-09-07.intel-module-plan.md §10 迁移执行约定）。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterable, Sequence

import pandas as pd
from sqlalchemy import text

from app.intel.core.logging import get_logger

logger = get_logger(__name__)

#: INTL_* 因子代码（与 factorize/factors.py 生产侧保持一致）
INTEL_FACTOR_CODES: tuple[str, ...] = (
    "INTL_MENTION_HEAT_5",
    "INTL_SENTIMENT_10",
    "INTL_RESONANCE_5",
    "INTL_FIRST_MENTION",
    "INTL_AUTHOR_CONVICTION",
    "INTL_STYLE_MATCH",
)

DEFAULT_FACTOR_VERSION = "v1"
DEFAULT_PROMPT_VERSION = "v2"

#: 面板空表结构（保证降级时返回的列一致，调用方无需判空）
PANEL_COLUMNS = [
    "symbol",
    "trade_date",
    "factor_code",
    "value",
    "available_at",
    "factor_version",
    "prompt_version",
]

_table_exists: bool | None = None  # 进程内缓存，避免每次查询都扫 information_schema


def _empty_panel() -> pd.DataFrame:
    """空面板（列齐全，调用方无需判空）"""
    return pd.DataFrame(columns=PANEL_COLUMNS)


def _ensure_dt(v) -> datetime | None:
    """把 date / datetime / ISO 字符串归一为 datetime（date 按当日 00:00）"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            try:
                return datetime.strptime(v, "%Y-%m-%d")
            except ValueError:
                return None
    return None


def reset_table_cache() -> None:
    """清掉表存在性缓存（测试用）"""
    global _table_exists
    _table_exists = None


def intel_factors_available(engine=None) -> bool:
    """intel.factor_values 是否可读。

    任何异常都视为不可用（返回 False），**不抛** —— 保证对 dc 主链路零影响。
    """
    global _table_exists
    if _table_exists is not None:
        return _table_exists

    try:
        if engine is None:
            from app.intel.store import get_engine

            engine = get_engine()
        with engine.connect() as c:
            n = c.execute(
                text(
                    """
                    SELECT count(*) FROM information_schema.tables
                    WHERE table_schema = 'intel' AND table_name = 'factor_values'
                    """
                )
            ).scalar()
        _table_exists = bool(n)
    except Exception as exc:  # noqa: BLE001 —— 可选依赖，失败即降级
        logger.warning("intel 因子表不可用（按可选依赖降级，不影响主链路）: %s", exc)
        _table_exists = False

    return _table_exists


def load_factor_panel(
    codes: Sequence[str] | None = None,
    symbols: Iterable[str] | None = None,
    start: str | date | None = None,
    end: str | date | None = None,
    as_of: date | datetime | str | None = None,
    factor_version: str = DEFAULT_FACTOR_VERSION,
    prompt_version: str | None = DEFAULT_PROMPT_VERSION,
    engine=None,
) -> pd.DataFrame:
    """读取 INTL_* 因子长表面板。

    参数
    ----
    codes : 因子代码列表，None 表示全部 INTL_* 因子
    symbols : 标的白名单，None 表示不限
    start / end : trade_date 区间（含端点）
    as_of : **决策时刻**，按 ``available_at <= as_of`` 过滤（防前视）。None 表示不过滤
    factor_version / prompt_version : 版本选择；prompt_version=None 表示不限制
        （调用方须明确，避免隐式混版）

    返回
    ----
    DataFrame，列见 ``PANEL_COLUMNS``。表不可用 / 查询异常 / 无数据均返回空表，不抛异常。
    """
    if not intel_factors_available(engine=engine):
        return _empty_panel()

    want = list(codes) if codes else list(INTEL_FACTOR_CODES)
    if not want:
        return _empty_panel()

    syms = list(symbols) if symbols is not None else None
    as_of_dt = _ensure_dt(as_of)

    # 动态拼 IN (:c0, :c1, ...)：代码数量固定且由本模块控制，无注入风险
    code_params = {f"c{i}": c for i, c in enumerate(want)}
    code_in = ", ".join(f":c{i}" for i in range(len(want)))

    conds = [f"factor_code IN ({code_in})", "factor_version = :fv"]
    params: dict = {**code_params, "fv": factor_version}

    if prompt_version is not None:
        conds.append("prompt_version = :pv")
        params["pv"] = prompt_version
    if syms:
        sp = {f"s{i}": s for i, s in enumerate(syms)}
        conds.append(f"symbol IN ({', '.join(f':s{i}' for i in range(len(syms)))})")
        params.update(sp)
    if start is not None:
        conds.append("trade_date >= CAST(:start AS date)")
        params["start"] = start
    if end is not None:
        conds.append("trade_date <= CAST(:end AS date)")
        params["end"] = end
    if as_of_dt is not None:
        # 防前视核心：只取决策时刻之前已可观测的因子值
        conds.append("available_at <= CAST(:as_of AS timestamptz)")
        params["as_of"] = as_of_dt

    sql = text(
        f"""
        SELECT symbol, trade_date, factor_code, value, available_at,
               factor_version, prompt_version
        FROM intel.factor_values
        WHERE {' AND '.join(conds)}
        ORDER BY trade_date, symbol, factor_code
        """
    )

    try:
        if engine is None:
            from app.intel.store import get_engine

            engine = get_engine()
        with engine.connect() as c:
            rows = c.execute(sql, params).mappings().all()
    except Exception as exc:  # noqa: BLE001 —— 可选依赖，失败即降级
        logger.warning("读取 intel 因子失败（按可选依赖降级）: %s", exc)
        return _empty_panel()

    if not rows:
        return _empty_panel()

    df = pd.DataFrame([dict(r) for r in rows])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df


def to_wide(panel: pd.DataFrame, code: str) -> pd.DataFrame:
    """长表 → 宽表：index=trade_date，columns=symbol，values=因子值"""
    if panel is None or panel.empty:
        return pd.DataFrame()
    sub = panel[panel["factor_code"] == code]
    if sub.empty:
        return pd.DataFrame()
    return sub.pivot_table(
        index="trade_date", columns="symbol", values="value", aggfunc="last"
    ).sort_index()


def merge_intel_factors(
    df: pd.DataFrame,
    codes: Sequence[str] | None = None,
    as_of: date | datetime | str | None = None,
    factor_version: str = DEFAULT_FACTOR_VERSION,
    prompt_version: str | None = DEFAULT_PROMPT_VERSION,
    engine=None,
) -> tuple[pd.DataFrame, list[str]]:
    """把 INTL_* 因子合并进因子面板（回测 / 选股消费入口）。

    ``df`` 需含 ``symbol`` 与 ``trade_date`` 两列（trade_date 也可由索引提供）。
    合并后每个因子代码新增一列；缺失填 NaN（intel 缺席时列仍然存在，值为空）。

    返回 ``(合并后的 df, warnings)`` —— 不抛异常。
    """
    warnings: list[str] = []
    if df is None or df.empty:
        return df, warnings

    work = df.copy()
    if "trade_date" not in work.columns:
        if work.index.name in ("trade_date", "timestamp", "date"):
            work = work.reset_index().rename(columns={work.index.name: "trade_date"})
        else:
            warnings.append("面板缺少 trade_date 列，跳过 INTL_* 因子合并")
            return df, warnings
    if "symbol" not in work.columns:
        warnings.append("面板缺少 symbol 列，跳过 INTL_* 因子合并")
        return df, warnings

    want = list(codes) if codes else list(INTEL_FACTOR_CODES)

    if not intel_factors_available(engine=engine):
        warnings.append("intel 因子不可用（可选依赖缺失），已按不含 INTL_* 因子的行为继续")
        for code in want:
            if code not in work.columns:
                work[code] = pd.NA
        return work, warnings

    panel = load_factor_panel(
        codes=want,
        symbols=work["symbol"].dropna().unique().tolist(),
        as_of=as_of,
        factor_version=factor_version,
        prompt_version=prompt_version,
        engine=engine,
    )

    work["_td"] = pd.to_datetime(work["trade_date"])

    for code in want:
        if code not in work.columns:
            work[code] = pd.NA
        sub = panel[panel["factor_code"] == code]
        if sub.empty:
            warnings.append(f"{code} 在给定条件下无数据（填 NaN）")
            continue
        sub = sub.copy()
        sub["_td"] = pd.to_datetime(sub["trade_date"])
        # 同一 (symbol, trade_date) 取最后一条（同版本内应唯一）
        sub = sub.sort_values("available_at").drop_duplicates(
            subset=["symbol", "_td"], keep="last"
        )
        m = dict(zip(zip(sub["symbol"], sub["_td"]), sub["value"]))
        work[code] = [
            m.get((s, t), pd.NA) for s, t in zip(work["symbol"], work["_td"])
        ]

    if panel.empty:
        warnings.append("intel.factor_values 无匹配数据（行情窗口或版本不匹配）")

    return work.drop(columns=["_td"]), warnings
