"""组合盘后估值（backend 定时任务）

职责（docs/memo/2026-09-02.md §五 + 用户口径）：
- backend 每个交易日盘后从 data-cleaner 拉最新价（market_proxy.latest_prices），
  更新各策略持仓的 last_price / market_value / unrealized_pnl；
- 按策略（每策略 = 一个独立资金池 / 基金产品）计算市值与当日 / 累计收益；
- 把快照写入 portfolio_daily_values（strategy_id 非空），供 dashboard 直接读取
  各策略市值曲线与收益率，无需实时重算因子或行情。
"""
import logging
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.quant import Strategy
from app.models.trading import MODE_PAPER, PortfolioDailyValue, TradingAccount
from app.services import trading_repository as repo
from app.services.factor_strategy_proxy import instrument_metadata
from app.services.market_proxy import MarketProxyError, latest_prices

logger = logging.getLogger(__name__)


async def _value_one_strategy(
    db: AsyncSession, mode: str, strategy_id: int, as_of: date
) -> dict:
    """对单个策略（独立资金池）做盘后估值并落快照。"""
    account = (
        await db.execute(
            select(TradingAccount).where(
                TradingAccount.mode == mode, TradingAccount.strategy_id == strategy_id
            )
        )
    ).scalars().first()
    if account is None:
        return {"strategy_id": strategy_id, "skipped": True, "reason": "无对应账户"}

    positions = await repo.list_positions(
        db, mode, account_id=account.id, strategy_id=strategy_id
    )
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
            logger.warning("回填标的主数据失败 strategy=%s mode=%s: %s", strategy_id, mode, e)

    try:
        prices = await latest_prices(db, symbols) if symbols else {}
    except MarketProxyError as e:
        logger.error("盘后估值取价失败 strategy=%s mode=%s: %s", strategy_id, mode, e)
        return {"strategy_id": strategy_id, "error": f"取价失败: {e}"}

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

    cash = float(account.cash_balance or 0)
    total_assets = cash + total_mv

    prev = (
        await db.execute(
            select(PortfolioDailyValue)
            .where(
                PortfolioDailyValue.mode == mode,
                PortfolioDailyValue.strategy_id == strategy_id,
                PortfolioDailyValue.value_date < as_of,
            )
            .order_by(PortfolioDailyValue.value_date.desc())
            .limit(1)
        )
    ).scalars().first()

    initial = float(account.initial_capital or 0)
    daily_return = (
        (total_assets / prev.total_assets - 1) if prev and prev.total_assets else None
    )
    cumulative_return = (total_assets / initial - 1) if initial else None

    rec = (
        await db.execute(
            select(PortfolioDailyValue).where(
                PortfolioDailyValue.mode == mode,
                PortfolioDailyValue.strategy_id == strategy_id,
                PortfolioDailyValue.value_date == as_of,
            )
        )
    ).scalars().first()
    if rec is None:
        rec = PortfolioDailyValue(mode=mode, strategy_id=strategy_id, value_date=as_of)
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
        "strategy_id": strategy_id,
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
    db: AsyncSession, mode: str = MODE_PAPER, strategy_id: int | None = None, as_of: date | None = None
) -> Any:
    """盘后估值入口。

    - strategy_id 给定：只对单个策略估值；
    - strategy_id 为 None：遍历所有启用策略，逐策略估值（每策略独立资金池）。

    返回单个 dict 或 dict 列表。取价失败仅记录告警并返回 error，不抛出
    （不阻断调度器）。
    """
    await repo.ensure_trading_tables()
    vd = as_of or date.today()

    if strategy_id is not None:
        return await _value_one_strategy(db, mode, strategy_id, vd)

    strategies = (
        await db.execute(select(Strategy).where(Strategy.is_active.is_(True)))
    ).scalars().all()
    results: list[dict] = []
    for s in strategies:
        results.append(await _value_one_strategy(db, mode, s.id, vd))
    await db.commit()
    return results
