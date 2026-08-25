"""因子相关性矩阵与多因子组合回测（P1 接口，步骤8）"""
import pandas as pd
from fastapi import APIRouter, HTTPException

from app.api.v1.schemas import BacktestRequest, CorrelationRequest
from app.factors.registry import get_factor
from app.storage.parquet_store import parquet_store

router = APIRouter(prefix="/factor", tags=["因子分析"])


@router.post("/correlation")
async def factor_correlation(req: CorrelationRequest):
    """计算给定因子代码列表的相关性矩阵

    读取最新一期因子 Parquet，按 symbol 对齐横截面相关性。
    """
    if len(req.codes) < 2:
        raise HTTPException(status_code=400, detail="至少需 2 个因子")

    series_list = []
    for code in req.codes:
        try:
            category = get_factor(code).category  # 真实类别目录名
        except Exception:
            raise HTTPException(status_code=404, detail=f"因子不存在: {code}") from None
        df = parquet_store.load_latest(category)
        if df is None or code not in df.columns:
            raise HTTPException(status_code=404, detail=f"因子值未落库: {code}")
        s = df[code]
        s.name = code
        series_list.append(s)

    corr = pd.concat(series_list, axis=1).corr().round(4)
    # 构造 JSON 安全的相关性矩阵（NaN -> None），兼容各 pandas 版本
    correlation = {}
    for idx in corr.index:
        row = corr.loc[idx]
        correlation[idx] = {
            col: (None if pd.isna(val) else float(val)) for col, val in row.items()
        }
    return {"codes": req.codes, "correlation": correlation}


@router.post("/backtest")
async def factor_backtest(req: BacktestRequest):
    """多因子等权组合回测（简化）

    weights 与 codes 等长；组合得分 = Σ w_i * zscore(factor_i)
    按组合得分十分位分层，返回多/空组合累计净值（示意）。
    """
    if len(req.codes) != len(req.weights):
        raise HTTPException(status_code=400, detail="codes 与 weights 长度不一致")

    combined = None
    for code, w in zip(req.codes, req.weights, strict=False):
        category = get_factor(code).category
        df = parquet_store.load_latest(category)
        if df is None or code not in df.columns:
            raise HTTPException(status_code=404, detail=f"因子值未落库: {code}")
        s = df[code]
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
    return {
        "codes": req.codes,
        "weights": req.weights,
        "longShortReturn": float(long_ret - short_ret),
        "finalEquity": float(equity.iloc[-1]) if len(equity) else 1.0,
        "sampleSize": int(len(combined)),
    }
