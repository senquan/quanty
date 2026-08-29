"""流水线触发与状态查询（P0 接口，步骤5）"""
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException

from app.api.v1.schemas import PipelineRunRequest, PipelineRunResponse
from app.core.exceptions import IngestionError
from app.core.logging import get_logger
from app.factors.registry import compute_factor, list_factors
from app.ingestion.registry import get_source
from app.pipeline.runner import CleaningPipeline
from app.storage.db import log_pipeline_run
from app.storage.parquet_store import parquet_store

logger = get_logger(__name__)
router = APIRouter(prefix="/pipeline", tags=["流水线"])


def _save_quarantine(raw, source: str, symbol: str, start: str, end: str, freq: str) -> None:
    """流水线失败时保留输入快照到 QUARANTINE_DIR，便于排查（§8.2）"""
    try:
        import pandas as pd

        from app.core.config import settings

        qdir = Path(settings.QUARANTINE_DIR)
        qdir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        tag = f"{source}_{symbol or 'all'}_{start}_{end}_{freq}"
        if raw is not None and isinstance(raw, pd.DataFrame) and not raw.empty:
            path = qdir / f"quarantine_{stamp}_{tag}.parquet"
            raw.to_parquet(path, index=False)
        else:
            path = qdir / f"quarantine_{stamp}_{tag}.empty"
            path.write_text("empty or missing input", encoding="utf-8")
        logger.warning("流水线失败，已保留输入快照", extra={"snapshot": str(path)})
    except Exception as e:
        logger.warning(f"保留 quarantine 快照失败: {e}")

_pipeline = CleaningPipeline()
_last_report: dict | None = None


async def run_default_pipeline(
    source: str = "csv",
    symbol: str = "",
    start: str = "2020-01-01",
    end: str | None = None,
    freq: str = "1d",
    csv_path: str | None = None,
) -> dict:
    """执行：拉取 → 清洗 → 计算全部因子 → 写入 Parquet → 写 DB 日志

    供 API 与定时任务共用。失败时保留输入快照到 quarantine 目录便于排查。
    """
    global _last_report
    from app.core import metrics

    end = end or datetime.now().strftime("%Y-%m-%d")
    start_ts = time.time()

    try:
        if source == "csv":
            if not csv_path:
                raise ValueError("csv 源需要 csvPath")
            raw = get_source("csv").fetch(path=csv_path, freq=freq)
        else:
            try:
                raw = get_source(source).fetch(symbol, start, end, freq)
            except IngestionError as e:
                raise HTTPException(status_code=502, detail=str(e)) from e

        if raw.empty:
            raise HTTPException(status_code=502, detail="数据源返回空")

        cleaned, report = _pipeline.run(raw)

        date = cleaned["timestamp"].max().strftime("%Y-%m-%d")
        for category in {f["category"] for f in list_factors()}:
            factor_df = cleaned[["symbol", "timestamp"]].copy()
            for meta in list_factors(category):
                factor_df[meta["code"]] = compute_factor(meta["code"], cleaned).values
            parquet_store.save(category, date, factor_df.set_index("timestamp"))

        _last_report = {**report, "date": date}

        # 刷新 Redis 缓存（factor:status + 各因子最新值），失败不影响主流程
        try:
            from app.storage import cache

            await cache.publish_status(_last_report)
            for category in {f["category"] for f in list_factors()}:
                df = parquet_store.load(date, category)
                if df is None:
                    continue
                # 取最后一根 K 线的因子值（行索引是 timestamp，symbol 为列之一）
                last_row = df.iloc[-1]
                for code in df.columns:
                    if code in ("symbol",):
                        continue
                    val = last_row[code]
                    if pd.notna(val):
                        await cache.cache_factor_latest(code, [{"symbol": code, "value": float(val)}])
        except Exception as e:
            logger.warning(f"Redis 缓存刷新失败: {e}")

        try:
            await log_pipeline_run(report["rows_in"], report["rows_out"], _last_report)
        except Exception as e:
            logger.warning(f"写 pipeline_runs 失败: {e}")
        logger.info("流水线任务完成", extra={"task": "pipeline_run", "rows_out": report["rows_out"]})

        metrics.record_pipeline(start_ts, ok=True, rows_out=report["rows_out"])
        metrics.set_gauge("factors_registered", float(len(list_factors())))
        return _last_report
    except Exception:
        metrics.record_pipeline(start_ts, ok=False, rows_out=0)
        # 失败保留输入快照
        raw_df = raw if "raw" in dir() else None
        ri = int(len(raw_df)) if raw_df is not None else 0
        _save_quarantine(raw_df, source, symbol, start, end, freq)
        try:
            await log_pipeline_run(ri, 0, {"error": str(sys.exc_info()[1])[:500]}, status="failed")
        except Exception as e:
            logger.warning(f"写 pipeline_runs(failed) 失败: {e}")
        raise



@router.post("/run", response_model=PipelineRunResponse)
async def run_pipeline(req: PipelineRunRequest):
    """手动触发：拉取 → 清洗 → 计算全部因子 → 写入 Parquet"""
    try:
        report = await run_default_pipeline(
            source=req.source,
            symbol=req.symbol,
            start=req.start,
            end=req.end,
            freq=req.freq,
            csv_path=req.csvPath,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return PipelineRunResponse(
        status="success",
        rowsIn=report["rows_in"],
        rowsOut=report["rows_out"],
        durationMs=report["duration_ms"],
        report=report["steps"],
    )


@router.get("/status")
async def pipeline_status():
    """返回最近一次流水线运行状态与报告"""
    return _last_report or {"status": "never_run"}
