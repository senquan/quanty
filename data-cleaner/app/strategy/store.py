"""因子选股策略的持久化（factor 域表，data-cleaner 持有）

全部为 async 函数；同步上下文（定时任务线程）用 db.run_async 调用。
JSON 字段统一用 json.dumps 绑定并显式 ::jsonb 转换（与 data-cleaner 现有风格一致）。
"""
import json
from datetime import date, datetime
from typing import Any

from app.storage import db


def _to_date(v) -> date | None:
    """把 'YYYY-MM-DD' 字符串转为 date 对象（Postgres DATE 列需要 date，不是 str）。"""
    if v is None or isinstance(v, date):
        return v
    if isinstance(v, datetime):
        return v.date()
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except Exception:  # noqa: BLE001
        return None


async def create_strategy(
    name: str, description: str | None, config: dict, owner: str | None, is_active: bool
) -> dict:
    async with db.current_session() as s:
        row = (
            await s.execute(
                db.text(
                    """
                    INSERT INTO factor.factor_strategies
                        (name, description, config, is_active, owner)
                    VALUES (:name, :desc, CAST(:cfg AS jsonb), :active, :owner)
                    RETURNING id, name, description, config, is_active, owner,
                              created_at, updated_at
                    """
                ),
                {
                    "name": name,
                    "desc": description,
                    "cfg": json.dumps(config),
                    "active": is_active,
                    "owner": owner,
                },
            )
        ).mappings().first()
        await s.commit()
        return dict(row)


async def list_strategies(active_only: bool = False) -> list[dict]:
    async with db.current_session() as s:
        sql = (
            "SELECT id, name, description, config, is_active, owner, "
            "created_at, updated_at FROM factor.factor_strategies"
        )
        if active_only:
            sql += " WHERE is_active = TRUE"
        sql += " ORDER BY id DESC"
        rows = (await s.execute(db.text(sql))).mappings().all()
        return [dict(r) for r in rows]


async def get_strategy(sid: int) -> dict | None:
    async with db.current_session() as s:
        row = (
            await s.execute(
                db.text(
                    "SELECT id, name, description, config, is_active, owner, "
                    "created_at, updated_at FROM factor.factor_strategies WHERE id=:id"
                ),
                {"id": sid},
            )
        ).mappings().first()
        return dict(row) if row else None


async def update_strategy(sid: int, **fields) -> dict | None:
    allowed = {"name", "description", "config", "is_active", "owner"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not sets:
        return await get_strategy(sid)
    params = {"id": sid}
    set_clauses = []
    for k, v in sets.items():
        if k == "config":
            set_clauses.append(f"{k}=CAST(:_{k} AS jsonb)")
            params[f"_{k}"] = json.dumps(v)
        else:
            set_clauses.append(f"{k}=:{k}")
            params[k] = v
    async with db.current_session() as s:
        await s.execute(
            db.text(
                f"UPDATE factor.factor_strategies SET {', '.join(set_clauses)}, "
                f"updated_at=now() WHERE id=:id"
            ),
            params,
        )
        await s.commit()
        return await get_strategy(sid)


async def delete_strategy(sid: int) -> bool:
    async with db.current_session() as s:
        res = await s.execute(
            db.text("DELETE FROM factor.factor_strategies WHERE id=:id"),
            {"id": sid},
        )
        await s.commit()
        return res.rowcount > 0


async def save_backtest(
    sid: int,
    start: str | None,
    end: str | None,
    metrics: dict,
    nav: list,
    rebalances: list,
    warnings: list,
) -> int:
    async with db.current_session() as s:
        row = (
            await s.execute(
                db.text(
                    """
                    INSERT INTO factor.factor_strategy_backtests
                        (strategy_id, start_date, end_date, metrics, nav, rebalances, warnings)
                    VALUES (:sid, :st, :en, CAST(:m AS jsonb), CAST(:nav AS jsonb), CAST(:reb AS jsonb), CAST(:w AS jsonb))
                    RETURNING id
                    """
                ),
                {
                    "sid": sid,
                    "st": _to_date(start),
                    "en": _to_date(end),
                    "m": json.dumps(metrics),
                    "nav": json.dumps(nav),
                    "reb": json.dumps(rebalances),
                    "w": json.dumps(warnings),
                },
            )
        ).mappings().first()
        await s.commit()
        return int(row["id"])


async def list_backtests(sid: int) -> list[dict]:
    async with db.current_session() as s:
        rows = (
            await s.execute(
                db.text(
                    "SELECT id, strategy_id, start_date, end_date, metrics, "
                    "created_at FROM factor.factor_strategy_backtests "
                    "WHERE strategy_id=:id ORDER BY id DESC"
                ),
                {"id": sid},
            )
        ).mappings().all()
        return [dict(r) for r in rows]


async def get_backtest(bid: int) -> dict | None:
    async with db.current_session() as s:
        row = (
            await s.execute(
                db.text(
                    "SELECT id, strategy_id, start_date, end_date, metrics, "
                    "nav, rebalances, warnings, created_at "
                    "FROM factor.factor_strategy_backtests WHERE id=:id"
                ),
                {"id": bid},
            )
        ).mappings().first()
        return dict(row) if row else None


async def save_execution(
    sid: int, rebalance_date: str, trade_date: str | None,
    target_count: int, orders_placed: int, amount: float,
    status: str, detail: dict,
) -> None:
    async with db.current_session() as s:
        await s.execute(
            db.text(
                """
                INSERT INTO factor.factor_strategy_executions
                    (strategy_id, rebalance_date, trade_date, target_count,
                     orders_placed, amount, status, detail)
                VALUES (:sid, :rd, :td, :tc, :op, :amt, :st, CAST(:d AS jsonb))
                ON CONFLICT (strategy_id, rebalance_date) DO UPDATE SET
                    trade_date = EXCLUDED.trade_date,
                    target_count = EXCLUDED.target_count,
                    orders_placed = EXCLUDED.orders_placed,
                    amount = EXCLUDED.amount,
                    status = EXCLUDED.status,
                    detail = EXCLUDED.detail
                """
            ),
            {
                "sid": sid,
                "rd": _to_date(rebalance_date),
                "td": _to_date(trade_date),
                "tc": target_count,
                "op": orders_placed,
                "amt": amount,
                "st": status,
                "d": json.dumps(detail),
            },
        )
        await s.commit()


async def list_executions(sid: int, limit: int = 50) -> list[dict]:
    async with db.current_session() as s:
        rows = (
            await s.execute(
                db.text(
                    "SELECT id, strategy_id, rebalance_date, trade_date, "
                    "target_count, orders_placed, amount, status, detail, created_at "
                    "FROM factor.factor_strategy_executions "
                    "WHERE strategy_id=:id ORDER BY rebalance_date DESC LIMIT :lim"
                ),
                {"id": sid, "lim": limit},
            )
        ).mappings().all()
        return [dict(r) for r in rows]


async def get_execution(sid: int, rebalance_date: str) -> dict | None:
    async with db.current_session() as s:
        row = (
            await s.execute(
                db.text(
                    "SELECT id, status FROM factor.factor_strategy_executions "
                    "WHERE strategy_id=:id AND rebalance_date=:rd"
                ),
                {"id": sid, "rd": _to_date(rebalance_date)},
            )
        ).mappings().first()
        return dict(row) if row else None
