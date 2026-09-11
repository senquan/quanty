"""流水线失败路径测试：
- pandera 校验失败应抛错
- API 层失败时应保留输入快照到 quarantine 目录
- 失败运行应写入 pipeline_runs(status='failed')
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

from app.api.v1 import pipeline as pipeline_api
from app.core.config import settings
from app.pipeline.runner import CleaningPipeline
from app.pipeline.schema_check import validate_cleaned
from app.storage import db

QUARANTINE_DIR = Path(settings.QUARANTINE_DIR)


def _make_invalid_df():
    """通过 6 步清洗、但 pandera 结构校验失败（high < low）"""
    return pd.DataFrame(
        {
            "symbol": ["X", "X"],
            "timestamp": pd.to_datetime(["2023-01-01", "2023-01-02"]),
            "open": [10.0, 11.0],
            "high": [9.0, 10.0],   # 故意 < low
            "low": [12.0, 13.0],
            "close": [10.5, 11.5],
            "volume": [1000.0, 1100.0],
            "source": ["csv", "csv"],
            "freq": ["1d", "1d"],
        }
    )


def test_pandera_rejects_high_lt_low():
    """high<low 的脏数据应在清洗流水线中被 pandera 校验拦截（抛错→触发隔离）"""
    with pytest.raises(Exception):
        CleaningPipeline().run(_make_invalid_df())


def test_quarantine_snapshot_on_failure():
    """API 层 _save_quarantine 应将失败输入落盘"""
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    raw = _make_invalid_df()
    pipeline_api._save_quarantine(raw, "csv", "X", "2023-01-01", "2023-01-02", "1d")
    snaps = list(QUARANTINE_DIR.glob("quarantine_*"))
    assert snaps, "失败时应生成隔离快照"
    # 清理本次快照
    for p in snaps:
        p.unlink()


def test_log_pipeline_run_failed():
    """失败运行应写入 pipeline_runs(status='failed')"""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    async def _run():
        # 用独立 engine，避免与全局 engine 的连接池并发竞争
        eng = create_async_engine(db.engine.url, future=True)
        async with eng.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO factor.pipeline_runs (rows_in, rows_out, report, status, finished_at) "
                    "VALUES (:ri, :ro, :rep, 'failed', now())"
                ),
                {"ri": 2, "ro": 0, "rep": "{}"},
            )
        async with eng.connect() as c:
            rows = await c.execute(
                text(
                    "SELECT status FROM factor.pipeline_runs "
                    "WHERE status='failed' ORDER BY finished_at DESC LIMIT 1"
                )
            )
            res = rows.fetchall()
        await eng.dispose()
        return res

    res = asyncio.run(_run())
    assert res and res[0][0] == "failed"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
