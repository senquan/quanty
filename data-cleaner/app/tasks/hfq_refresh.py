"""增量回填 factor.raw_bars.hfq_close（D-2 根治）

为什么需要
----------
每日增量入库走 pandadata（无复权因子接口，``adjust="pre"`` 只写 qfq 的 close），
``hfq_close`` **天然写入 NULL**；而 ``backfill_hfq.py`` 是一次性脚本（2026-09-06 跑完
5,557 只标的就再没跑过）⇒ 每过一天多缺一天。2026-09-10 审计时发现已断供 3 天
（14,098 行），下游 ``_p3_eval.py`` 因此把「价格列是空的」误报成「行情未覆盖」。

原理
----
hfq 与 qfq 只差一个**每标的常数** k = hfq / qfq（实测 cv ≈ 1e-14，浮点精度级别），
故每标的只需 1 条 UPDATE，不必逐行重拉：

    UPDATE factor.raw_bars SET hfq_close = close * :k WHERE symbol = :s

k 取已有重叠日期 ``hfq_close/close`` 的**中位数**抗源端精度噪声（不用末日单点）。

⚠️ 前提：回填区间内**不能发生新的除权** —— 除权会让 hfq 整体重锚、k 随之改变。
首次运行建议先用 ``backfill_hfq_incremental.py --verify``（akshare 真值抽样）确认。

幂等：只更新 ``hfq_close IS NULL`` 的行，重复运行无副作用。
"""
from __future__ import annotations

from sqlalchemy import Engine, create_engine, text

from app.core.config import settings
from app.core.logging import get_logger

# ⚠️ raw_bars.freq 取值是 '1d' 不是 'daily'，写错会静默返回 0 行（不报错）
FREQ_DAILY = "1d"

logger = get_logger(__name__)

_engine: Engine | None = None


def _sync_url() -> str:
    """DATABASE_URL 的 asyncpg driver 降级为 psycopg2（同步写，不影响主 async 用法）"""
    url = settings.DATABASE_URL
    if "+asyncpg" in url:
        return url.replace("+asyncpg", "+psycopg2")
    if not url.startswith("postgresql+psycopg2") and url.startswith("postgresql"):
        return url.replace("postgresql", "postgresql+psycopg2", 1)
    return url


def get_engine() -> Engine:
    """进程内共享的同步引擎（与 app.intel.store.get_engine 同款，独立缓存避免跨模块依赖）"""
    global _engine
    if _engine is None:
        _engine = create_engine(_sync_url(), pool_pre_ping=True, future=True)
    return _engine

_MISSING_SQL = """
SELECT count(*) AS rows_missing,
       count(DISTINCT symbol) AS syms_missing,
       min(timestamp)::date AS d_min,
       max(timestamp)::date AS d_max
FROM factor.raw_bars
WHERE freq = :frq AND hfq_close IS NULL
"""

_UPDATE_SQL = """
WITH k AS (
    SELECT symbol,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY hfq_close / close) AS kv
    FROM factor.raw_bars
    WHERE freq = :frq AND hfq_close IS NOT NULL AND close > 0
      -- ⚠️ 这个过滤不能省：写入侧每写一批就调一次定向补，若 CTE 全表聚合
      -- （613 万行 GROUP BY），每次入库都要几十秒 —— 增量链路会被拖垮。
      AND (CAST(:syms AS text[]) IS NULL OR symbol = ANY(CAST(:syms AS text[])))
    GROUP BY symbol
)
UPDATE factor.raw_bars b
SET hfq_close = b.close * k.kv
FROM k
WHERE b.symbol = k.symbol
  AND b.freq = :frq
  AND b.hfq_close IS NULL
  AND b.close > 0
  -- :syms 为 NULL 时补全库；传列表则只补指定标的（用于新股定向补 / 单测隔离）
  AND (CAST(:syms AS text[]) IS NULL OR b.symbol = ANY(CAST(:syms AS text[])))
"""

_MISSING_SYMBOL_SQL = """
SELECT DISTINCT symbol
FROM factor.raw_bars
WHERE freq = :frq AND hfq_close IS NULL
ORDER BY symbol
"""

# 从未回填过（无历史 hfq）⇒ 算不出 k，需单独全量重拉
_ORPHAN_SQL = """
SELECT DISTINCT b.symbol
FROM factor.raw_bars b
WHERE b.freq = :frq AND b.hfq_close IS NULL
  AND (CAST(:syms AS text[]) IS NULL OR b.symbol = ANY(CAST(:syms AS text[])))
  AND NOT EXISTS (
      SELECT 1 FROM factor.raw_bars x
      WHERE x.symbol = b.symbol AND x.freq = :frq
        AND x.hfq_close IS NOT NULL AND x.close > 0
  )
"""


def count_missing(engine: Engine | None = None) -> dict:
    """统计 hfq_close 缺失情况"""
    engine = engine or get_engine()
    with engine.connect() as c:
        r = c.execute(text(_MISSING_SQL), {"frq": FREQ_DAILY}).mappings().first()
    return {
        "rows": r["rows_missing"],
        "symbols": r["syms_missing"],
        "date_min": r["d_min"],
        "date_max": r["d_max"],
    }


def _fill_one_batch(engine: Engine, symbols: list[str] | None) -> tuple[int, list[str]]:
    """跑一批（symbols=None 时补全库）。返回 (更新行数, 孤儿标的)"""
    with engine.begin() as c:
        updated = c.execute(
            text(_UPDATE_SQL), {"frq": FREQ_DAILY, "syms": symbols}
        ).rowcount
        orphans = [
            r[0]
            for r in c.execute(
                text(_ORPHAN_SQL), {"frq": FREQ_DAILY, "syms": symbols}
            ).all()
        ]
    return updated, orphans


def backfill(
    engine: Engine | None = None,
    symbols: list[str] | None = None,
    batch: int = 200,
) -> dict:
    """回填缺失的 hfq_close。返回 {updated, orphans, before, after}

    :param symbols: 只补指定标的（新股定向补 / 单测隔离）；None = 全库
    :param batch:   全库回填时的分批大小

    ⚠️ 全库回填**必须分批**：一次全表 ``percentile_cont`` 聚合在 600 万行上要跑
    20 分钟以上，还会和并行的入库/定时任务互锁（2026-09-10 实测：3 个回填查询
    互相阻塞，其中一个卡了 21 分钟）。分批后每批只聚合 batch 只标的，秒级完成。
    """
    engine = engine or get_engine()
    # 定向补（写入侧调用）时跳过全库 count —— 那是全表扫描，不该进热路径
    before = count_missing(engine) if symbols is None else None

    if symbols is not None:
        updated, orphans = _fill_one_batch(engine, symbols)
    else:
        with engine.connect() as c:
            all_syms = [
                r[0]
                for r in c.execute(text(_MISSING_SYMBOL_SQL), {"frq": FREQ_DAILY}).all()
            ]
        updated, orphans = 0, []
        for i in range(0, len(all_syms), batch):
            u, o = _fill_one_batch(engine, all_syms[i : i + batch])
            updated += u
            orphans.extend(o)

    after = count_missing(engine) if symbols is None else None

    if symbols is None:
        logger.info(
            f"hfq_close 增量回填：{before['rows']} → {after['rows']} 行"
            f"（写入 {updated} 行，无历史 hfq 的标的 {len(orphans)} 只）",
            extra={
                "task": "hfq_refresh",
                "updated": updated,
                "missing_before": before["rows"],
                "missing_after": after["rows"],
                "orphans": len(orphans),
            },
        )
    elif updated:
        logger.info(
            f"hfq_close 写入后定向回填 {len(symbols)} 只标的，写入 {updated} 行",
            extra={"task": "hfq_refresh", "updated": updated},
        )
    if orphans:
        logger.warning(
            f"{len(orphans)} 只标的无历史 hfq，拿不到 k，需单独全量重拉："
            f"{orphans[:10]}{' ...' if len(orphans) > 10 else ''}",
            extra={"task": "hfq_refresh", "orphans": orphans[:50]},
        )
    return {
        "updated": updated,
        "orphans": orphans,
        "before": before,
        "after": after,
    }
