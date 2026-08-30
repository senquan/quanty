"""全市场因子库构建（批量任务）

流程：从 PG factor.raw_bars 读取已入库日线 → 逐标的清洗 → 计算全部因子 →
按「category/日期」写 parquet 横截面（index=symbol，列=因子代码）。

产物：{FACTOR_DATA_DIR}/{category}/{YYYY-MM-DD}.parquet
- 每行一个标的，每列一个因子（该类别）
- 不同类别文件在同一日期 index 一致，可直接按 symbol 对齐做相关性/回测
  （app.api.v1.analytics 依赖这一点）

用法：python run_factor_build.py [--start 2026-01-01] [--end 2026-08-28] [--category momentum]
"""
import time

import pandas as pd

from app.core.logging import get_logger
from app.factors.formula import compile_formula
from app.factors.registry import compute_factor, list_factors
from app.pipeline.runner import CleaningPipeline
from app.storage.parquet_store import parquet_store
from app.storage.raw_store import repository

logger = get_logger(__name__)


def _merge_fundamental(panel: pd.DataFrame) -> pd.DataFrame:
    """把迁移 006 的基础数据合并进清洗后的 panel（symbol × timestamp）。

    - daily_basic：估值/换手/市值，按 (symbol, date) 左连接
    - finance_reports：成长（rev/eps 同比），按 ann_date 做 as-of 前向填充（防前视）
    表为空时跳过，保持向后兼容（缺列因子照旧返回 NaN）。
    """
    from app.storage import fundamental_store

    if panel.empty:
        return panel
    panel = panel.copy()
    panel["_d"] = panel["timestamp"].dt.normalize()

    start = panel["_d"].min().strftime("%Y-%m-%d")
    end = panel["_d"].max().strftime("%Y-%m-%d")

    # 1) daily_basic：估值 / 换手 / 市值
    db = fundamental_store.load_daily_basic(start=start, end=end)
    if not db.empty:
        db = db.copy()
        db["_d"] = pd.to_datetime(db["trade_date"])
        db = db.rename(columns={"dv_ttm": "div_yield"}).drop(columns=["trade_date"])
        keep = [
            "symbol", "_d", "pe_ttm", "pb", "ps_ttm", "div_yield",
            "turnover_rate", "turnover_rate_f", "total_mv", "circ_mv",
        ]
        db = db[[c for c in keep if c in db.columns]]
        db = db.drop_duplicates(subset=["symbol", "_d"], keep="last")
        panel = panel.merge(db, on=["symbol", "_d"], how="left")

    # 2) finance_reports：成长同比，按披露日 as-of 前向填充
    fr = fundamental_store.load_finance_reports()
    if not fr.empty:
        fr = fr.copy()
        fr["ann_date"] = pd.to_datetime(fr["ann_date"])
        fr = fr.dropna(subset=["ann_date"]).sort_values("ann_date")
        right = fr[["symbol", "ann_date", "rev_growth_yoy", "eps_growth_yoy"]]
        left = panel.sort_values("_d")
        try:
            panel = pd.merge_asof(
                left, right, left_on="_d", right_on="ann_date",
                by="symbol", direction="backward",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"财报 as-of 合并失败: {e}")
            panel = left

    return panel.drop(columns=["_d", "ann_date"], errors="ignore")


def load_custom_definitions() -> list[dict]:
    """读取 DB 里的自定义因子定义（factor.definitions，带 formula）。

    本函数是同步上下文（脚本/线程池中执行），通过 db.run_async 复用
    本线程的事件循环——asyncpg 连接池绑定 loop，不能每次新建。
    """
    import app.storage.db as db

    try:
        return db.run_async(db.list_factor_definitions()) or []
    except Exception as e:  # noqa: BLE001
        logger.warning(f"读取自定义因子定义失败: {e}")
        return []


def build_factor_library(
    symbols: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    categories: list[str] | None = None,
    progress_every: int = 500,
) -> dict:
    """构建（重建）因子库，返回汇总。

    :param symbols: 限定标的；None 表示全部
    :param start/end: 限定日期区间（含）
    :param categories: 限定因子类别；None 表示全部
    """
    t0 = time.time()
    pipeline = CleaningPipeline()

    raw = repository.load_all(start=start, end=end, symbols=symbols)
    if raw.empty:
        return {"status": "error", "reason": "factor.raw_bars 无数据"}

    raw["timestamp"] = pd.to_datetime(raw["timestamp"])
    n_symbols = raw["symbol"].nunique()
    logger.info(
        "因子库构建开始",
        extra={"task": "factor_build", "rows": len(raw), "symbols": n_symbols},
    )

    # 是否局部构建（指定了 symbols）——决定截面文件是合并还是整份覆盖
    partial = bool(symbols)

    # 1) 逐标的清洗（流水线按单标的语义设计，TimeAlign 等步骤依赖单标的连续序列）
    parts: list[pd.DataFrame] = []
    ok = error = 0
    errors: list[str] = []
    for i, (sym, g) in enumerate(raw.groupby("symbol", sort=False), 1):
        try:
            cleaned, _ = pipeline.run(g.sort_values("timestamp").reset_index(drop=True))
        except Exception as e:  # noqa: BLE001
            error += 1
            if len(errors) < 10:
                errors.append(f"{sym}: {str(e)[:80]}")
            continue
        if cleaned is None or cleaned.empty:
            error += 1
            continue
        parts.append(cleaned)
        ok += 1
        if i % progress_every == 0:
            logger.info(
                "清洗进度",
                extra={"task": "factor_build", "done": i, "total": n_symbols, "ok": ok},
            )

    if not parts:
        return {"status": "error", "reason": "清洗后无有效数据", "errors": errors}

    panel = pd.concat(parts, ignore_index=True)
    panel["timestamp"] = pd.to_datetime(panel["timestamp"])

    # 合并基础数据（估值/换手/市值/成长），供 VAL_/GRO_/换手/市值因子使用
    panel = _merge_fundamental(panel)

    # 2) 计算内置因子（因子内部按 symbol 分组，全市场一起算即可）
    metas = list_factors()
    if categories:
        metas = [m for m in metas if m["category"] in set(categories)]
    computed = failed = 0
    failed_codes: list[str] = []
    cat_of_code: dict[str, str] = {}
    for meta in metas:
        try:
            panel[meta["code"]] = compute_factor(meta["code"], panel).values
            computed += 1
            cat_of_code[meta["code"]] = meta["category"]
        except Exception as e:  # noqa: BLE001
            failed += 1
            failed_codes.append(f"{meta['code']}: {str(e)[:60]}")
            logger.warning(f"因子计算失败 {meta['code']}: {e}")

    # 3) 计算自定义（公式）因子：DB 定义里带 formula 且不在内置注册表中的
    #    否则新建的自定义因子永远不会有因子值，相关性/回测会把它判为“不存在”
    builtin_codes = {m["code"] for m in metas}
    custom_ok = custom_failed = 0
    for d in load_custom_definitions():
        code = d.get("code")
        formula = (d.get("formula") or "").strip()
        if not code or not formula or code in builtin_codes:
            continue
        try:
            panel[code] = compile_formula(formula)(panel).values
            cat_of_code[code] = d.get("category") or "custom"
            custom_ok += 1
        except Exception as e:  # noqa: BLE001
            custom_failed += 1
            failed_codes.append(f"{code}(自定义): {str(e)[:60]}")
            logger.warning(f"自定义因子计算失败 {code}: {e}")

    # 4) 按「日期 × 类别」写横截面
    day_str = panel["timestamp"].dt.strftime("%Y-%m-%d")
    cats = sorted({cat_of_code[c] for c in cat_of_code if c in panel.columns})
    files = 0
    rows_written = 0
    for date, idx in day_str.groupby(day_str).groups.items():
        day = panel.loc[idx]
        for cat in cats:
            codes = [
                c for c in cat_of_code
                if cat_of_code.get(c) == cat and c in panel.columns
            ]
            if not codes:
                continue
            xs = day.set_index("symbol")[codes]
            xs = xs[~xs.index.duplicated(keep="last")]
            if partial:
                # 局部构建（指定了 symbols）：只覆盖这些标的，保留同日其它标的
                parquet_store.save_cross_section(cat, date, xs)
            else:
                # 全量重建：整份截面重算，直接覆盖
                parquet_store.save(cat, date, xs)
            files += 1
            rows_written += len(xs)

    duration = round(time.time() - t0, 1)
    summary = {
        "status": "done",
        "symbols_in": n_symbols,
        "symbols_ok": ok,
        "symbols_error": error,
        "factors_computed": computed,
        "factors_failed": failed,
        "custom_computed": custom_ok,
        "custom_failed": custom_failed,
        "categories": cats,
        "dates": len(day_str.unique()),
        "files": files,
        "rows_written": rows_written,
        "duration_s": duration,
        "sampleErrors": errors[:5],
        "failedFactors": failed_codes[:5],
    }
    logger.info("因子库构建完成", extra={"task": "factor_build", **summary})
    return summary
