"""因子相关性矩阵与多因子组合回测（P1 接口，步骤8）"""
import pandas as pd
from fastapi import APIRouter, HTTPException

import app.storage.db as db
from app.api.v1.schemas import BacktestRequest, CorrelationRequest
from app.factors.registry import get_factor
from app.storage.parquet_store import parquet_store

router = APIRouter(prefix="/factor", tags=["因子分析"])


async def _resolve_category(code: str) -> str | None:
    """解析因子的类别目录名。

    内置因子在 Python 注册表里；自定义因子只存在于 factor.definitions 表，
    因此两处都要查，否则自定义因子会被误判为“不存在”。
    """
    try:
        return get_factor(code).category
    except Exception:  # noqa: BLE001
        pass
    try:
        definition = await db.get_factor_definition(code)
    except Exception:  # noqa: BLE001
        return None
    return (definition or {}).get("category")


async def _load_series(codes: list[str]) -> tuple[list[pd.Series], list[str]]:
    """按 code 取最新一期因子值；返回 (可用序列, 缺失代码)。

    缺失原因分两类：因子定义不存在、或该因子尚未计算落盘（如新建的自定义因子）。
    这里选择跳过而非整体报错，避免一个坏因子毁掉整个矩阵。
    """
    series_list: list[pd.Series] = []
    missing: list[str] = []
    for code in codes:
        category = await _resolve_category(code)
        df = parquet_store.load_latest(category) if category else None
        if df is None or code not in df.columns:
            missing.append(code)
            continue
        s = df[code]
        s.name = code
        series_list.append(s)
    return series_list, missing


def _to_json_safe(corr: pd.DataFrame) -> dict:
    """构造 JSON 安全的相关性矩阵（NaN -> None）"""
    out: dict = {}
    for idx in corr.index:
        row = corr.loc[idx]
        out[idx] = {col: (None if pd.isna(val) else float(val)) for col, val in row.items()}
    return out


@router.post("/correlation")
async def factor_correlation(req: CorrelationRequest):
    """计算给定因子代码列表的相关性矩阵

    读取最新一期因子 Parquet，按 symbol 对齐横截面相关性。
    """
    if len(req.codes) < 2:
        raise HTTPException(status_code=400, detail="至少需 2 个因子")

    series_list, missing = await _load_series(req.codes)
    if len(series_list) < 2:
        raise HTTPException(
            status_code=400,
            detail=f"可用因子值不足（仅 {len(series_list)} 个），缺失: {', '.join(missing) or '无'}",
        )

    corr = pd.concat(series_list, axis=1).corr().round(4)
    return {
        "codes": [s.name for s in series_list],
        "correlation": _to_json_safe(corr),
        "missing": missing,
    }


@router.post("/backtest")
async def factor_backtest(req: BacktestRequest):
    """多因子等权组合回测（简化）

    weights 与 codes 等长；组合得分 = Σ w_i * zscore(factor_i)
    按组合得分十分位分层，返回多/空组合累计净值（示意）。
    """
    if len(req.codes) != len(req.weights):
        raise HTTPException(status_code=400, detail="codes 与 weights 长度不一致")

    series_list, missing = await _load_series(req.codes)
    if len(series_list) < 2:
        raise HTTPException(
            status_code=400,
            detail=f"可用因子值不足（仅 {len(series_list)} 个），缺失: {', '.join(missing) or '无'}",
        )

    # 权重按 code 顺序与可用序列对齐（缺失因子直接跳过）
    available = {s.name for s in series_list}
    weight_map = {c: w for c, w in zip(req.codes, req.weights, strict=False) if c in available}

    combined = None
    for s in series_list:
        w = weight_map.get(str(s.name), 1.0)
        z = (s - s.mean()) / (s.std() + 1e-9)
        combined = z if combined is None else combined + w * z

    combined = combined.dropna()
    if len(combined) < 10:
        raise HTTPException(
            status_code=400,
            detail=f"有效样本不足（{len(combined)} < 10），无法进行分层回测",
        )
    # 按组合得分分组（样本不足 10 组时自动降档）
    n_bins = min(10, len(combined) // 2) or 2
    try:
        groups = pd.qcut(combined.rank(method="first"), n_bins, labels=False)
    except ValueError:
        groups = pd.cut(combined.rank(method="first"), n_bins, labels=False)
    top = n_bins - 1
    long_ret = combined[groups == top].mean()
    short_ret = combined[groups == 0].mean()
    # 累计净值
    equity = (1 + combined).cumprod()
    used = [str(s.name) for s in series_list]
    return {
        # 只回实际参与计算的因子，避免把被跳过的（无因子值）因子也算进来
        "codes": used,
        "weights": [weight_map.get(c, 1.0) for c in used],
        "missing": missing,
        "longShortReturn": float(long_ret - short_ret),
        "finalEquity": float(equity.iloc[-1]) if len(equity) else 1.0,
        "sampleSize": int(len(combined)),
    }
