"""原始行情历史库 API（增量保存全 A 股日线）

- GET  /raw/universe    全 A 股代码池
- GET  /raw/{symbol}    读取某标的区间历史（start/end 可选）
- POST /raw/backfill    手动触发增量/全量回填（source, symbols?, full?）
- GET  /raw/status      最近一次回填摘要
"""
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.core.logging import get_logger
from app.ingestion.universe import get_a_share_universe
from app.storage import cache
from app.storage.raw_store import repository
from app.tasks import backfill as backfill_task

logger = get_logger(__name__)
router = APIRouter(prefix="/raw", tags=["raw"])

_PROGRESS_KEY = "raw_backfill_progress"


@router.get("/universe")
def get_universe() -> list[str]:
    try:
        return get_a_share_universe()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/{symbol}")
def get_raw(
    symbol: str,
    start: str | None = Query(None, description="YYYY-MM-DD"),
    end: str | None = Query(None, description="YYYY-MM-DD"),
) -> dict[str, Any]:
    df = repository.load(symbol, start, end)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"无 {symbol} 历史数据")
    recs = df.assign(timestamp=df["timestamp"].astype(str)).to_dict("records")
    return {"symbol": symbol, "count": len(recs), "rows": recs}


@router.post("/backfill")
def trigger_backfill(
    body: dict[str, Any],
    bg: BackgroundTasks,
) -> dict[str, Any]:
    source = body.get("source", "alphafeed")
    symbols = body.get("symbols")
    full = bool(body.get("full", False))

    def _run():
        summary = backfill_task.backfill_universe(
            source=source, symbols=symbols, full=full
        )
        try:
            cache.redis.set(_PROGRESS_KEY, summary, ex=86400)
        except Exception:  # noqa: BLE001
            pass

    bg.add_task(_run)
    return {"status": "accepted", "source": source, "full": full, "mode": "background"}


@router.get("/status")
def backfill_status() -> dict[str, Any]:
    try:
        val = cache.redis.get(_PROGRESS_KEY)
        if val:
            return val
    except Exception:  # noqa: BLE001
        pass
    return {"status": "no-run-yet"}
