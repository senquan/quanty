"""数据库访问层（异步 SQLAlchemy）

复用主后端 PostgreSQL，使用独立 `factor` schema（见 migrations/001）。
"""
import asyncio
import json
import threading
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# 后台线程（流水线/定时任务/因子策略计算）使用的独立引擎。
# 与主请求循环（engine）隔离，避免 asyncpg 连接池被绑到后台 loop 上，
# 导致 API 请求报 "attached to a different loop"。
engine_bg = create_async_engine(settings.DATABASE_URL, echo=False, future=True)
async_session_bg = async_sessionmaker(engine_bg, class_=AsyncSession, expire_on_commit=False)

# 当前 session 工厂：默认主引擎（API 请求循环）；run_async 执行期间临时切到后台引擎。
_session_factory = async_session


def current_session():
    """返回当前生效的 session 实例（主循环 / 后台循环切换）。"""
    return _session_factory()


# asyncpg 的连接池绑定创建它的事件循环：若每次调用都新建 loop 再关闭，
# 池里的连接会残留在已关闭的 loop 上，后续请求报
# "another operation is in progress"。故按线程缓存并复用同一个 loop。
_LOOPS: dict[int, asyncio.AbstractEventLoop] = {}
_LOOPS_LOCK = threading.Lock()


def run_async(coro) -> Any:
    """在「本线程固定的事件循环」中执行协程。

    同步任务（因子构建/评估/策略回测等，常跑在线程池里）调用本模块的异步仓储时，
    一律走这里；执行期间把 session 工厂切到后台引擎，避免污染主请求循环。
    """
    global _session_factory
    tid = threading.get_ident()
    with _LOOPS_LOCK:
        loop = _LOOPS.get(tid)
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            _LOOPS[tid] = loop
    prev = _session_factory
    _session_factory = async_session_bg
    try:
        return loop.run_until_complete(coro)
    finally:
        _session_factory = prev


async def log_pipeline_run(
    rows_in: int, rows_out: int, report: dict, status: str = "success"
) -> None:
    """写入一次流水线运行记录"""
    async with current_session() as session:
        await session.execute(
            text(
                """
                INSERT INTO factor.pipeline_runs
                    (rows_in, rows_out, report, status, finished_at)
                VALUES (:ri, :ro, :rep, :st, now())
                """
            ),
            {"ri": rows_in, "ro": rows_out, "rep": json.dumps(report), "st": status},
        )
        await session.commit()


async def get_last_pipeline_run() -> dict | None:
    """返回最近一次流水线运行记录（供 /qos 使用），无记录返回 None"""
    async with current_session() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT rows_in, rows_out, status, finished_at
                    FROM factor.pipeline_runs
                    ORDER BY finished_at DESC
                    LIMIT 1
                    """
                )
            )
        ).fetchone()
        if row is None:
            return None
        return {
            "rows_in": row.rows_in,
            "rows_out": row.rows_out,
            "status": row.status,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        }


async def apply_migrations() -> None:
    """建表（幂等）。真实环境中应由 alembic 管理，此处提供最小可用入口。

    执行 migrations/ 下所有 *.sql（001 因子库 + 002 raw_bars 历史行情库），
    按文件名顺序，语句以分号切分逐一执行（plpgsql 函数体内的分号不切分）。
    """
    from pathlib import Path

    # db.py 位于 <root>/app/storage/db.py，故 migrations 在 parents[2]
    # （原实现误用 parents[3]，指向仓库上级目录导致 glob 为空、迁移静默不执行）
    mig_dir = Path(__file__).resolve().parents[2] / "migrations"
    sql_files = sorted(mig_dir.glob("*.sql"))
    if not sql_files:
        raise FileNotFoundError(f"未找到迁移文件: {mig_dir}")
    stmts: list[str] = []
    for f in sql_files:
        sql = f.read_text(encoding="utf-8")
        buf: list[str] = []
        in_func = False  # 是否处于 plpgsql 函数体（$$ ... $$）
        for line in sql.splitlines():
            s = line.strip()
            if s.startswith("--"):
                continue
            buf.append(line)
            lower = s.lower()
            # 进入 plpgsql 函数体：CREATE FUNCTION 起始，直到 $$ LANGUAGE 结束
            if not in_func and lower.startswith("create or replace function"):
                in_func = True
                continue
            if in_func:
                if "$$ language" in lower and s.rstrip().endswith(";"):
                    in_func = False
                    stmt = "\n".join(buf).strip().rstrip(";").strip()
                    if stmt:
                        stmts.append(stmt)
                    buf = []
                continue
            if s.endswith(";"):
                stmt = "\n".join(buf).strip().rstrip(";").strip()
                if stmt:
                    stmts.append(stmt)
                buf = []
        tail = "\n".join(buf).strip().rstrip(";").strip()
        if tail:
            stmts.append(tail)
    async with engine.begin() as conn:
        for stmt in stmts:
            await conn.execute(text(stmt))
    return sql_files


# ---- 因子定义 (factor.definitions) ----

async def upsert_factor_definition(meta: dict, author: str = "system") -> None:
    """插入或更新因子定义（按 code 去重）"""
    async with current_session() as session:
        await session.execute(
            text(
                """
                INSERT INTO factor.definitions
                    (code, name, category, frequency, formula, data_sources,
                     author, description)
                VALUES (:code, :name, :cat, :freq, :formula, :ds, :author, :desc)
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name,
                    category = EXCLUDED.category,
                    frequency = EXCLUDED.frequency,
                    formula = EXCLUDED.formula,
                    data_sources = EXCLUDED.data_sources,
                    -- 未传 description 时保留原值，避免被清成空
                    description = COALESCE(EXCLUDED.description, factor.definitions.description)
                """
            ),
            {
                "code": meta["code"],
                "name": meta["name"],
                "cat": meta["category"],
                "freq": meta["frequency"],
                "formula": meta.get("formula"),
                "ds": json.dumps(meta.get("data_sources")),
                "author": author,
                "desc": meta.get("description"),
            },
        )
        await session.commit()


async def delete_factor_definition(code: str, author: str) -> bool:
    """删除因子定义；仅允许作者为 user 的自定义因子，返回是否删除成功"""
    async with current_session() as session:
        res = await session.execute(
            text(
                """
                DELETE FROM factor.definitions
                WHERE code = :code AND author = :author
                """
            ),
            {"code": code, "author": author},
        )
        await session.commit()
        return res.rowcount > 0


async def get_factor_definition(code: str) -> dict | None:
    """按 code 读取单个因子定义，不存在返回 None"""
    async with current_session() as session:
        row = await session.execute(
            text(
                """
                SELECT code, name, category, frequency, formula,
                       data_sources, author, status, created_at, description
                FROM factor.definitions WHERE code = :code
                """
            ),
            {"code": code},
        )
        r = row.mappings().first()
        return dict(r) if r else None


async def list_factor_definitions() -> list[dict]:
    """读取全部因子定义元数据"""
    async with current_session() as session:
        rows = await session.execute(
            text(
                """
                SELECT code, name, category, frequency, formula,
                       data_sources, author, status, created_at, description
                FROM factor.definitions ORDER BY category, code
                """
            )
        )
        return [dict(r._mapping) for r in rows]


# ---- 因子效能指标 (factor.metrics) ----

async def save_factor_metrics(code: str, as_of: str, metrics: dict) -> None:
    """写入/更新某因子某日效能指标"""
    from datetime import date, datetime

    if isinstance(as_of, str):
        as_of_date = datetime.strptime(as_of, "%Y-%m-%d").date()
    elif isinstance(as_of, datetime):
        as_of_date = as_of.date()
    else:
        as_of_date = as_of  # 已是 date
    async with current_session() as session:
        await session.execute(
            text(
                """
                INSERT INTO factor.metrics
                    (factor_code, as_of_date, ic_mean, ic_std, ir,
                     sharpe_ratio, max_drawdown, win_rate)
                VALUES (:code, :as_of, :icm, :ics, :ir, :sr, :mdd, :wr)
                ON CONFLICT (factor_code, as_of_date) DO UPDATE SET
                    ic_mean = EXCLUDED.ic_mean, ic_std = EXCLUDED.ic_std,
                    ir = EXCLUDED.ir, sharpe_ratio = EXCLUDED.sharpe_ratio,
                    max_drawdown = EXCLUDED.max_drawdown, win_rate = EXCLUDED.win_rate
                """
            ),
            {
                "code": code,
                "as_of": as_of_date,
                "icm": metrics.get("icMean"),
                "ics": metrics.get("icStd"),
                "ir": metrics.get("ir"),
                "sr": metrics.get("sharpeRatio"),
                "mdd": metrics.get("maxDrawdown"),
                "wr": metrics.get("winRate"),
            },
        )
        await session.commit()


async def get_factor_metrics(code: str) -> list[dict]:
    async with current_session() as session:
        rows = await session.execute(
            text(
                "SELECT * FROM factor.metrics WHERE factor_code = :code ORDER BY as_of_date DESC"
            ),
            {"code": code},
        )
        return [dict(r._mapping) for r in rows]


async def list_latest_factor_metrics() -> dict[str, dict]:
    """一次性读取全部因子的最新一期效能指标（按 factor_code 去重）。

    供因子列表批量挂载指标使用，避免逐因子查询造成的 N+1 慢请求。
    """
    async with current_session() as session:
        rows = await session.execute(
            text(
                """
                SELECT DISTINCT ON (factor_code)
                       factor_code, as_of_date, ic_mean, ic_std, ir,
                       sharpe_ratio, max_drawdown, win_rate
                FROM factor.metrics
                ORDER BY factor_code, as_of_date DESC
                """
            )
        )
        # rows.mappings() 返回的已是 RowMapping，直接 dict(r) 即可
        return {r["factor_code"]: dict(r) for r in rows.mappings()}
