"""清洗后行情查询（P0 新增接口，步骤5）"""
from datetime import datetime

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from app.core.exceptions import IngestionError
from app.ingestion.registry import get_source

router = APIRouter(prefix="/data", tags=["数据"])


@router.get("/bars", summary="查询清洗后行情")
async def get_bars(
    symbol: str = Query(..., description="标的代码"),
    start: str | None = Query(None, description="起始日期 YYYY-MM-DD"),
    end: str | None = Query(None, description="结束日期 YYYY-MM-DD"),
    freq: str = Query("1d", description="频率"),
    source: str = Query("yfinance", description="数据源"),
):
    """拉取并经过清洗流水线后的行情

    注：Phase 1 直接拉取+清洗返回，未落库；落库在 Phase 2 步骤6 完成。
    """
    try:
        src = get_source(source)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        raw = src.fetch(symbol, start or "2020-01-01", end or datetime.now().strftime("%Y-%m-%d"), freq)
    except IngestionError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    if raw.empty:
        return {"symbol": symbol, "bars": []}

    # 仅返回关键列，避免 adj_* 等中间列泄露到前端
    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    out = raw[cols].copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"]).dt.strftime("%Y-%m-%dT%H:%M:%S")
    return {"symbol": symbol, "bars": out.to_dict(orient="records")}
