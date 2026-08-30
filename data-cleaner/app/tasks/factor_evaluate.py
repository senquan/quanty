"""因子效能评估（基于已落库的因子值 + 真实次日收益）

思路：
- 因子值：读取 data/factors/{category}/{date}.parquet 的横截面（index=symbol）
- 未来收益：从 factor.raw_bars 读取前复权收盘价，按 symbol 计算次日收益
- 逐交易日做**截面** Spearman 秩相关得到 IC 序列，再由 IC 序列推导各项指标

与 app.factors.evaluator.FactorEvaluator 的区别：
后者面向"无日期维度的扁平序列"（时序 IC，icStd/ir 恒为 0）；
这里用真实面板数据算截面 IC，能得到有意义的 icStd / IR / 胜率。

指标口径：
  icMean       IC 序列均值
  icStd        IC 序列标准差
  ir           icMean / icStd（未年化）
  sharpeRatio  icMean / icStd * sqrt(252)（IC 年化夏普）
  maxDrawdown  多空组合（每日最高十分位 - 最低十分位收益）净值最大回撤
  winRate      IC > 0 的交易日占比
"""
import time
from datetime import datetime

import pandas as pd

from app.core.config import settings
from app.core.logging import get_logger
from app.factors.registry import get_factor
from app.storage import db
from app.storage.parquet_store import parquet_store
from app.storage.raw_store import repository

logger = get_logger(__name__)

MIN_DATES = 20          # 至少需要的交易日数
MIN_CROSS_SECTION = 50  # 每个截面至少需要的标的数


def build_forward_returns() -> pd.DataFrame:
    """按 symbol 计算次日收益，返回 DataFrame(index=date, columns=symbol)。"""
    raw = repository.load_all()
    if raw.empty:
        return pd.DataFrame()

    raw["timestamp"] = pd.to_datetime(raw["timestamp"])
    raw = raw.sort_values(["symbol", "timestamp"])
    # close 已是前复权价，直接用它算下一交易日收益
    raw["fwd"] = raw.groupby("symbol", sort=False)["close"].shift(-1) / raw["close"] - 1
    raw["date"] = raw["timestamp"].dt.strftime("%Y-%m-%d")

    fwd = raw.dropna(subset=["fwd"]).pivot(
        index="date", columns="symbol", values="fwd"
    )
    return fwd.sort_index()


def load_factor_panel(categories: list[str] | None = None) -> pd.DataFrame:
    """把各日期的因子横截面拼成 DataFrame(index=date, columns=[symbol, factor...])。

    返回长表：MultiIndex (date, symbol) × 因子列。
    """
    base = settings.factor_data_path
    cat_panels = []
    for cat_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        if categories and cat_dir.name not in categories:
            continue
        frames = []
        for f in sorted(cat_dir.glob("*.parquet")):
            df = pd.read_parquet(f)
            if df.empty:
                continue
            df = df.copy()
            df.index.name = "symbol"
            df["date"] = f.stem
            frames.append(df.reset_index().set_index(["date", "symbol"]))
        if not frames:
            continue
        # 同一类别内按日期纵向拼接，得到该类别的 (date, symbol) 面板
        cat_panel = pd.concat(frames, axis=0)
        cat_panels.append(cat_panel[~cat_panel.index.duplicated(keep="first")])

    if not cat_panels:
        return pd.DataFrame()
    # 各类别面板索引一致，横向合并成完整因子面板
    panel = pd.concat(cat_panels, axis=1)
    return panel.sort_index()


def _ic_metrics(ic: pd.Series, long_short: pd.Series) -> dict:
    """由 IC 序列与多空日收益序列推导效能指标。

    口径说明：
    - IC / IR 基于截面 IC 序列（IR = IC均值 / IC标准差，不年化，业界惯例）
    - 夏普比率与最大回撤基于**多空组合日收益**年化，这才是因子夏普的标准定义；
      若拿 IC 序列直接乘 sqrt(252) 会得到 9+ 这种失真数值
    - 胜率 = IC > 0 的交易日占比
    """
    ic = ic.dropna()
    if len(ic) < MIN_DATES:
        return {}
    mean = float(ic.mean())
    std = float(ic.std())

    ls = long_short.dropna()
    sharpe = 0.0
    mdd = 0.0
    if len(ls) > 1:
        ls_std = float(ls.std())
        sharpe = float(ls.mean()) / ls_std * (252**0.5) if ls_std > 1e-12 else 0.0
        cum = (1 + ls).cumprod()
        peak = cum.cummax()
        mdd = float(((cum - peak) / peak).min()) if peak.iloc[-1] > 1e-9 else -1.0

    return {
        "icMean": mean,
        "icStd": std,
        "ir": (mean / std) if std > 1e-12 else 0.0,
        "sharpeRatio": sharpe,
        "maxDrawdown": mdd,
        "winRate": float((ic > 0).mean()),
    }


def evaluate_factor(code: str, panel: pd.DataFrame, fwd: pd.DataFrame) -> dict:
    """计算单个因子的效能指标；样本不足返回空 dict。"""
    if code not in panel.columns:
        return {}
    # 因子值 -> (date × symbol) 矩阵
    values = panel[code].unstack("symbol")
    common_dates = values.index.intersection(fwd.index)
    common_syms = values.columns.intersection(fwd.columns)
    if len(common_dates) < MIN_DATES or len(common_syms) < MIN_CROSS_SECTION:
        return {}

    if len(common_dates) < MIN_DATES or len(common_syms) < MIN_CROSS_SECTION:
        logger.warning(
            f"因子 {code} 样本不足，跳过: "
            f"dates={len(common_dates)}(需>={MIN_DATES}) "
            f"symbols={len(common_syms)}(需>={MIN_CROSS_SECTION})"
        )
        return {}

    v = values.loc[common_dates, common_syms]
    r = fwd.loc[common_dates, common_syms]

    # 只保留有效样本足够多的交易日：早期日期可能只有个位数标的，
    # 少量样本的秩相关会剧烈震荡，把 IC 标准差撑大、把 IR 压到失真
    valid = v.notna() & r.notna()
    enough = valid.sum(axis=1) >= MIN_CROSS_SECTION
    v = v.where(enough)
    r = r.where(enough)

    # 截面 Spearman 相关（逐交易日）
    ic = v.corrwith(r, axis=1, method="spearman")
    if int(ic.notna().sum()) < MIN_DATES:
        logger.warning(
            f"因子 {code} IC 有效交易日不足，跳过: "
            f"{int(ic.notna().sum())} < {MIN_DATES}"
        )
        return {}

    # 多空组合：每日按因子值十分位，最高组 - 最低组的平均次日收益
    long_short = []
    for date in common_dates:
        row = v.loc[date].dropna()
        if len(row) < MIN_CROSS_SECTION:
            long_short.append(float("nan"))
            continue
        ret = r.loc[date].reindex(row.index)
        try:
            groups = pd.qcut(row.rank(method="first"), 10, labels=False)
        except ValueError:
            long_short.append(float("nan"))
            continue
        long_short.append(float(ret[groups == 9].mean() - ret[groups == 0].mean()))
    ls = pd.Series(long_short, index=common_dates)

    metrics = _ic_metrics(ic, ls)
    if metrics:
        metrics["_samples"] = int(len(ic))
    return metrics


def evaluate_all_factors(
    as_of: str | None = None,
    categories: list[str] | None = None,
    codes: list[str] | None = None,
) -> dict:
    """对因子库中全部（或指定）因子做评估并落库 factor.metrics。"""
    import asyncio

    import app.storage.db as db

    t0 = time.time()
    as_of = as_of or datetime.now().strftime("%Y-%m-%d")

    panel = load_factor_panel(categories)
    if panel.empty:
        return {"status": "error", "reason": "因子库为空，请先构建因子库"}

    fwd = build_forward_returns()
    if fwd.empty:
        return {"status": "error", "reason": "无法计算未来收益（raw_bars 为空）"}

    targets = [c for c in panel.columns if c != "symbol"]
    if codes:
        targets = [c for c in targets if c in set(codes)]

    # 1) 先纯内存算完（同步）
    ok = skipped = 0
    results: list[dict] = []
    pending: list[tuple[str, dict]] = []
    for code in targets:
        try:
            metrics = evaluate_factor(code, panel, fwd)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"因子评估失败 {code}: {e}")
            metrics = {}
        if not metrics:
            skipped += 1
            continue
        samples = metrics.pop("_samples", 0)
        pending.append((code, metrics))
        results.append({"code": code, "samples": samples, **metrics})

    # 2) 再统一落库：所有写操作必须在同一个事件循环里
    #    （asyncpg 连接绑定 loop，逐因子新建 loop 会报 "another operation is in progress"）
    async def _ensure_definition(code: str) -> None:
        """factor.metrics.factor_code 外键引用 factor.definitions(code)，
        内置因子只在 Python 注册表里，落指标前需先补一条定义。

        这里写入注册表的真实元数据（而不是 EVAL 占位），
        避免因子列表接口把内置因子的类别/名称显示错。
        """
        if await db.get_factor_definition(code):
            return
        try:
            meta = get_factor(code).get_metadata()
        except Exception:  # noqa: BLE001
            meta = None
        await db.upsert_factor_definition(
            {
                "code": code,
                "name": (meta or {}).get("name", code),
                "category": (meta or {}).get("category", "custom"),
                "frequency": (meta or {}).get("frequency", "Daily"),
                "formula": "",
                "data_sources": (meta or {}).get("data_sources", []),
            },
            author="system",
        )

    async def _save_all() -> list[str]:
        failed: list[str] = []
        for code, metrics in pending:
            try:
                await _ensure_definition(code)
                await db.save_factor_metrics(code, as_of, metrics)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"因子指标落库失败 {code}: {e}")
                failed.append(code)
        return failed

    failed_codes = db.run_async(_save_all())
    ok = len(pending) - len(failed_codes)
    skipped += len(failed_codes)
    if failed_codes:
        results = [r for r in results if r["code"] not in set(failed_codes)]

    summary = {
        "status": "done",
        "as_of": as_of,
        "factors_total": len(targets),
        "factors_evaluated": ok,
        "factors_skipped": skipped,
        "dates": int(len(fwd)),
        "duration_s": round(time.time() - t0, 1),
        "top_by_ir": sorted(
            results, key=lambda x: abs(x.get("ir") or 0), reverse=True
        )[:5],
    }
    logger.info("因子效能评估完成", extra={"task": "factor_evaluate", **summary})
    return summary
