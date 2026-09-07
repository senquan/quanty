"""脚本策略回测（dc 端）。

按 ``docs/memo/architecture.md`` §0 的归口决策：
**回测在 dc，因为 A 股数据在 dc**（``factor.raw_bars``，5,555 只 / 611 万行）。
backend 的同类引擎挂在 yfinance / ccxt 上，拿不到 A 股 —— 这是「回测数据源为什么没有 A 股」的根因。

模块分工::

    market_rules.py  市场规则表（A 股：T+1 / 整手 100 / 涨跌停 / 费率）—— 唯一事实来源
    gate.py          闸口：只输出 Plan 或 Refusal，没有「带警告勉强跑」的第三种
    data.py          取数 + 复权口径（qfq / hfq）
    indicators.py    DataEnricher：给策略执行环境挂常用指标
    validator.py     策略代码 AST 校验
    engine.py        两遍执行 + 逐 bar 撮合
    service.py       编排入口（R3 的 HTTP 接口只是它的薄壳）

与 dc 因子选股回测（``app/strategy/engine.py``）的关系：
两者是不同范式（配置驱动横截面 vs 单标的脚本），**不合并**；
但市场规则只认 ``market_rules.py`` 这一份，避免两套事实来源。
"""

from app.backtest.data import BacktestDataError, load_bars
from app.backtest.engine import BacktestEngine
from app.backtest.gate import Plan, Refusal, STYLES, check_sample, plan_backtest
from app.backtest.indicators import DataEnricher
from app.backtest.market_rules import MARKETS, a_share_limit_pct, calc_fees, classify_symbol, normalize_symbol
from app.backtest.service import BacktestRefused, ScriptBacktestRequest, run_script_backtest
from app.backtest.validator import StrategyValidator

__all__ = [
    "BacktestEngine",
    "BacktestRefused",
    "BacktestDataError",
    "DataEnricher",
    "MARKETS",
    "Plan",
    "Refusal",
    "ScriptBacktestRequest",
    "StrategyValidator",
    "STYLES",
    "a_share_limit_pct",
    "calc_fees",
    "check_sample",
    "classify_symbol",
    "load_bars",
    "normalize_symbol",
    "plan_backtest",
    "run_script_backtest",
]
