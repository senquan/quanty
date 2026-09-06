"""组合盘后估值（backend 定时任务）

职责（docs/memo/2026-09-02.md §五 + 用户口径）：
- backend 每个交易日盘后从 data-cleaner 拉最新价（market_proxy.latest_prices），
  更新各组合持仓的 last_price / market_value / unrealized_pnl；
- 按组合（每组合 = 一个独立资金池 / 基金产品）计算市值与当日 / 累计收益；
- 把快照写入 portfolio_daily_values（portfolio_id 非空），供 dashboard 直接读取
  各组合市值曲线与收益率，无需实时重算因子或行情。
"""
import logging
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trading import MODE_PAPER, Portfolio, PortfolioDailyValue
from app.services import trading_repository as repo
from app.services.factor_strategy_proxy import instrument_metadata
from app.services.market_proxy import MarketProxyError, latest_prices

logger = logging.getLogger(__name__)


async def _value_one_portfolio(db: AsyncSession, portfolio: Portfolio, as_of: date) -> dict:
    """对单个组合（独立资金池）做盘后估值并落快照。"""
    pid = portfolio.id
    mode = portfolio.mode
    strategy_id = portfolio.strategy_id
    positions = await repo.list_positions(db, portfolio_id=pid)
    symbols = [p.symbol for p in positions if p.quantity and p.quantity > 0]

    # 顺带回填标的主数据（代码→中文名）：名字源来自 dc 只读元数据接口，
    # 在此「更新行情」的批量环节落库，不进 dashboard 加载路径（避免加载时查 dc）。
    # 仅对主表中缺失的代码取名字，已存在的跳过。
    if symbols:
        try:
            existing = await repo.get_instruments(db, symbols)
            missing = [s for s in symbols if s not in existing]
            if missing:
                meta = await instrument_metadata(db, missing)
                if meta:
                    await repo.upsert_instruments(
                        db,
                        [
                            {
                                "symbol": s,
                                "name": (m or {}).get("name") or s,
                                "industry": (m or {}).get("industry"),
                            }
                            for s, m in meta.items()
                        ],
                    )
        except Exception as e:  # noqa: BLE001  名字缺失不影响估值，仅告警
            logger.warning("回填标的主数据失败 portfolio=%s mode=%s: %s", pid, mode, e)

    try:
        prices = await latest_prices(db, symbols) if symbols else {}
    except MarketProxyError as e:
        logger.error("盘后估值取价失败 portfolio=%s mode=%s: %s", pid, mode, e)
        return {"portfolio_id": pid, "error": f"取价失败: {e}"}

    total_mv = 0.0
    # 盘后重定价前，把当前 last_price（即上一交易日收盘）快照为 prev_close
    old_close = {p.symbol: float(p.last_price or 0) for p in positions}
    for p in positions:
        price = prices.get(p.symbol)
        if price:
            p.prev_close = old_close.get(p.symbol, 0.0)
            p.last_price = price
            p.market_value = p.quantity * price
            p.unrealized_pnl = (price - p.avg_price) * p.quantity
        total_mv += float(p.market_value or 0)

    cash = float(portfolio.cash_balance or 0)
    total_assets = cash + total_mv

    prev = (
        await db.execute(
            select(PortfolioDailyValue)
            .where(
                PortfolioDailyValue.portfolio_id == pid,
                PortfolioDailyValue.value_date < as_of,
            )
            .order_by(PortfolioDailyValue.value_date.desc())
            .limit(1)
        )
    ).scalars().first()

    initial = float(portfolio.initial_capital or 0)
    daily_return = (
        (total_assets / prev.total_assets - 1) if prev and prev.total_assets else None
    )
    cumulative_return = (total_assets / initial - 1) if initial else None

    rec = (
        await db.execute(
            select(PortfolioDailyValue).where(
                PortfolioDailyValue.portfolio_id == pid,
                PortfolioDailyValue.value_date == as_of,
            )
        )
    ).scalars().first()
    if rec is None:
        rec = PortfolioDailyValue(
            portfolio_id=pid, strategy_id=strategy_id, mode=mode, value_date=as_of
        )
        db.add(rec)
    rec.cash_balance = round(cash, 2)
    rec.market_value = round(total_mv, 2)
    rec.total_assets = round(total_assets, 2)
    rec.daily_return = round(daily_return, 6) if daily_return is not None else None
    rec.cumulative_return = (
        round(cumulative_return, 6) if cumulative_return is not None else None
    )

    await db.commit()
    return {
        "portfolio_id": pid,
        "value_date": as_of.isoformat(),
        "cash_balance": rec.cash_balance,
        "market_value": rec.market_value,
        "total_assets": rec.total_assets,
        "daily_return": rec.daily_return,
        "cumulative_return": rec.cumulative_return,
        "priced_symbols": len(prices),
        "missing_symbols": len([s for s in symbols if s not in prices]),
    }


async def run_eod_valuation(
    db: AsyncSession,
    mode: str = MODE_PAPER,
    strategy_id: int | None = None,
    portfolio_id: int | None = None,
    as_of: date | None = None,
) -> Any:
    """盘后估值入口。

    - portfolio_id 给定：只对单个组合估值；
    - strategy_id 给定：对该策略绑定的首个组合估值；
    - 两者均为 None：遍历该模式下全部组合，逐资金池估值。

    每个组合是一个独立资金池。返回单个 dict 或 dict 列表。取价失败仅记录告警
    并返回 error，不抛出（不阻断调度器）。
    """
    await repo.ensure_trading_tables()
    vd = as_of or date.today()

    if portfolio_id is not None:
        portfolio = await repo.get_portfolio(db, portfolio_id)
        if portfolio is None:
            return {"portfolio_id": portfolio_id, "skipped": True, "reason": "无对应组合"}
        return await _value_one_portfolio(db, portfolio, vd)

    if strategy_id is not None:
        portfolio = (
            await db.execute(
                select(Portfolio).where(
                    Portfolio.mode == mode, Portfolio.strategy_id == strategy_id
                )
            )
        ).scalars().first()
        if portfolio is None:
            return {"strategy_id": strategy_id, "skipped": True, "reason": "无对应组合"}
        return await _value_one_portfolio(db, portfolio, vd)

    portfolios = await repo.list_portfolios(db, mode=mode)
    results: list[dict] = []
    for p in portfolios:
        results.append(await _value_one_portfolio(db, p, vd))
    await db.commit()
    return results
