"""脚本策略回测引擎 —— 单标的、用户写 Python（buy / sell）、逐 bar 撮合。

迁移自 ``backend/app/services/backtest_engine.py``（2026-09-06 P0/P1 修复版，
27 项引擎测试 + 8 项闸口测试）。核心逻辑一行未改，只做了两件事：

1. **抽掉数据源**。backend 版自带 yfinance / ccxt 取数；这里只吃一个 DataFrame ——
   取数是 ``app/backtest/data.py`` 的事，引擎不关心数据是哪儿来的。
2. **加停牌拒单**。dc 有停牌语义（``volume == 0`` 且价格不动 = 无成交），
   backend 版没有这一条，会在停牌日照样撮合。

执行分两遍：
  第一遍 —— 跑策略代码，只收集「买/卖信号」，并实时维护 position / capital，
            使策略里的 get_position() 能拿到那一刻的持仓。
  第二遍 —— 把每个信号绑定到具体 bar，再逐 bar 撮合，生成组合价值曲线。

为什么要分两遍：策略是批式写法（自己 for 循环全历史），一次性把信号全发完，
引擎拿不到「这笔信号发生在第几根 bar」。只有把信号先收集、再按 bar 归并，
才能给每笔成交一个真实的成交时间，并算出逐日组合价值。

bar 绑定优先级：
  ① 显式指定：buy(price, qty, bar=i) / sell(price, qty, bar=i)
  ② 价格前向匹配：从上一笔的 bar 往后找收盘价等于该成交价的第一根 bar。
     策略几乎总是写 buy(close.iloc[i])，所以这个匹配是精确的。

市场规则：
  传入 ``market`` 后，第二遍撮合会真的执行 T+1、涨跌停、整手、停牌与交易费用。
  因此持仓不再是单个数字，而是带买入 bar 的**批次队列** ——
  T+1 要能回答「这批是今天买的吗」，标量做不到。
  被规则挡下的信号进 ``rejections``，不静默丢弃：
  静默丢弃的话，报告描述的就是「删掉被拒信号后的策略」，而不是用户写的那个。
"""

from __future__ import annotations

import inspect
import math
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.backtest.indicators import DataEnricher
from app.backtest.market_rules import MarketRules, a_share_limit_pct, calc_fees


class BacktestEngine:
    """回测引擎核心类。"""

    def __init__(
        self,
        initial_capital: float = 100_000,
        market: Optional[MarketRules] = None,
        symbol: Optional[str] = None,
        allow_short: bool = False,
    ):
        self.initial_capital = float(initial_capital)
        self.capital = float(initial_capital)
        self.position = 0
        self.trades: List[Dict] = []
        self.daily_returns: List[float] = []
        self.portfolio_values: List[float] = []

        # market=None 表示「不套用任何市场规则」——等价于 T+0、无涨跌停、零费用，
        # 与 P1 之前的行为一致，用于不想被规则约束的纯算法验证。
        self.market = market
        self.symbol = symbol
        self.allow_short = bool(allow_short)
        self._lot = self._resolve_lot()
        self._limit_pct = self._resolve_limit_pct()

        # 单次运行的内部状态
        self._signals: List[Dict] = []
        self._closes: Optional[np.ndarray] = None
        self._index: Optional[pd.Index] = None
        self._infer_stats: Dict[str, int] = {"explicit": 0, "price_matched": 0}

    # ── 市场规则解析 ────────────────────────────────────

    def _resolve_lot(self) -> int:
        """最小交易单位。0 / 1 表示不限。"""
        if self.market is None:
            return 1
        return max(0, int(self.market.lot_size)) or 1

    def _resolve_limit_pct(self) -> Optional[float]:
        """涨跌停幅度。None = 无涨跌停。"""
        if self.market is None or not self.market.has_price_limit:
            return None
        return a_share_limit_pct(self.symbol or "")

    # ── 执行 ──────────────────────────────────────────────

    def execute_strategy(
        self,
        strategy_code: str,
        data: pd.DataFrame,
        market: Optional[MarketRules] = None,
        symbol: Optional[str] = None,
    ) -> Dict:
        """执行策略代码并逐 bar 撮合。

        ``market`` / ``symbol`` 可在此覆盖构造函数的值
        （闸口的 ``Plan.to_engine_config()`` 就是这么传进来的）。
        """
        if market is not None:
            self.market = market
        if symbol is not None:
            self.symbol = symbol
        self._lot = self._resolve_lot()
        self._limit_pct = self._resolve_limit_pct()

        if data is None or len(data) == 0:
            raise ValueError("行情数据为空，无法回测")

        try:
            enriched = DataEnricher.add_technical_indicators(data)

            closes = (
                pd.to_numeric(enriched["close"], errors="coerce")
                .fillna(0.0)
                .to_numpy(dtype=float)
            )
            if len(closes) == 0:
                raise ValueError("行情数据为空，无法回测")

            volumes = (
                pd.to_numeric(enriched["volume"], errors="coerce").to_numpy(dtype=float)
                if "volume" in enriched.columns
                else None
            )

            self._closes = closes
            self._volumes = volumes
            self._index = enriched.index
            self._signals = []
            self.capital = float(self.initial_capital)
            self.position = 0

            strategy_globals = {
                "np": np,
                "pd": pd,
                "data": enriched,
                "buy": self._buy,
                "sell": self._sell,
                "get_position": lambda: self.position,
                "get_capital": lambda: self.capital,
            }

            # 第一遍：执行策略，收集信号
            # 模块级写法 —— exec 时就跑完
            exec(strategy_code, strategy_globals)  # noqa: S102

            # def on_data(data, context) 写法 —— 前端模板生成的就是这种，
            # 只 exec 的话函数定义了却从不会被调用，必须显式调用一次。
            on_data = strategy_globals.get("on_data")
            if callable(on_data):
                context = {
                    "initial_capital": self.initial_capital,
                    "get_capital": lambda: self.capital,
                    "get_position": lambda: self.position,
                    "symbol": self.symbol,
                }
                n_params = len(inspect.signature(on_data).parameters)
                if n_params >= 2:
                    on_data(enriched, context)
                elif n_params == 1:
                    on_data(enriched)
                else:
                    on_data()

            # 第二遍：绑定 bar + 逐 bar 撮合
            self._bind_bars()
            return self._simulate()

        except Exception as e:
            raise ValueError(f"Strategy execution failed: {e}") from e

    # ── 信号收集（第一遍） ────────────────────────────────

    def _buy(self, price: float, quantity: int = None, bar: int = None):
        """买入信号。bar 为该笔信号对应的 bar 下标，可省略（由价格匹配推断）。"""
        try:
            price = float(price)
        except (TypeError, ValueError):
            return
        if not math.isfinite(price) or price <= 0:
            return

        if quantity is None:
            quantity = int(self.capital // price)
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            return
        if quantity <= 0:
            return

        # 整手约束：A 股只能买 100 股的整数倍。
        # 第一遍就按整手收，策略里的 get_position() 才不会跟实际撮合差太远。
        if self._lot > 1:
            rounded = (quantity // self._lot) * self._lot
            if rounded <= 0:
                # 不足一手：仍然记一笔信号，让第二遍**明确拒单**。
                # 这里如果直接 return，这笔单子就无声无息地消失了 ——
                # 报告描述的就是「删掉它之后的策略」，而不是用户写的那个。
                self._signals.append({
                    "type": "buy", "price": price, "quantity": quantity,
                    "bar": int(bar) if bar is not None else None,
                })
                return
            quantity = rounded
        if quantity <= 0:
            return

        # 资金不足时按可用资金截断，而不是整笔丢弃
        cost = price * quantity
        if cost > self.capital:
            requested = quantity
            quantity = int(self.capital // price)
            if self._lot > 1:
                quantity = (quantity // self._lot) * self._lot
            if quantity <= 0:
                # 买不起也要记一笔，让第二遍**明确拒单** ——
                # 与「不足一手」同理：静默丢弃的话，报告描述的就是另一个策略。
                self._signals.append({
                    "type": "buy", "price": price, "quantity": requested,
                    "bar": int(bar) if bar is not None else None,
                })
                return
            cost = price * quantity

        self.capital -= cost
        self.position += quantity
        self._signals.append({
            "type": "buy", "price": price, "quantity": quantity,
            "bar": int(bar) if bar is not None else None,
        })

    def _sell(self, price: float, quantity: int = None, bar: int = None):
        """卖出信号。bar 为该笔信号对应的 bar 下标，可省略（由价格匹配推断）。"""
        try:
            price = float(price)
        except (TypeError, ValueError):
            return
        if not math.isfinite(price) or price <= 0:
            return

        if quantity is None:
            quantity = self.position
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            return
        if quantity <= 0:
            return
        if quantity > self.position:
            quantity = self.position
        if quantity <= 0:
            return

        self.capital += price * quantity
        self.position -= quantity
        self._signals.append({
            "type": "sell", "price": price, "quantity": quantity,
            "bar": int(bar) if bar is not None else None,
        })

    # ── bar 绑定 ─────────────────────────────────────────

    def _bind_bars(self) -> None:
        """给每个信号确定它发生在第几根 bar。"""
        closes = self._closes
        last = len(closes) - 1
        cursor = 0
        self._infer_stats = {"explicit": 0, "price_matched": 0}

        for sig in self._signals:
            if sig["bar"] is not None and 0 <= sig["bar"] <= last:
                cursor = sig["bar"]
                self._infer_stats["explicit"] += 1
                continue
            sig["bar"] = self._match_bar(sig["price"], cursor, last)
            cursor = sig["bar"]
            self._infer_stats["price_matched"] += 1

        # 稳定排序：按 bar 归并，同一 bar 内保持策略的原始调用顺序
        self._signals.sort(key=lambda s: s["bar"])

    def _match_bar(self, price: float, start: int, last: int) -> int:
        """从 start 往后找收盘价等于 price 的第一根 bar；找不到就取最接近的。"""
        if start > last:
            return last
        seg = self._closes[start:]
        exact = np.flatnonzero(np.isclose(seg, price, rtol=0.0, atol=1e-9))
        if len(exact):
            return start + int(exact[0])
        return start + int(np.argmin(np.abs(seg - price)))

    # ── 撮合（第二遍） ───────────────────────────────────

    def _simulate(self) -> Dict:
        """逐 bar 撮合，生成 trades / portfolio_values / daily_returns。

        持仓用**批次队列**表示（每批带买入 bar），因为 T+1 要能回答
        「这批是不是今天买的」。FIFO 平仓时跳过当日买入的批次。
        """
        closes = self._closes
        volumes = getattr(self, "_volumes", None)
        index = self._index
        n = len(closes)

        rules = self.market
        lot = self._lot
        limit_pct = self._limit_pct
        t0 = rules.same_day_roundtrip if rules is not None else True

        by_bar: Dict[int, List[Dict]] = {}
        for sig in self._signals:
            by_bar.setdefault(sig["bar"], []).append(sig)

        cash = float(self.initial_capital)
        lots: List[Dict] = []          # 未平仓买入批次，队首最早
        trades: List[Dict] = []
        rejections: List[Dict] = []
        portfolio_values: List[float] = []
        total_fees = 0.0

        def reject(i: int, sig_type: str, price: float, qty: int, reason: str) -> None:
            rejections.append({
                "bar": int(i),
                "date": pd.to_datetime(index[i]).strftime("%Y-%m-%d"),
                "type": sig_type,
                "price": float(price),
                "quantity": int(qty),
                "reason": reason,
            })

        for i in range(n):
            # 停牌：无成交量的 bar 买不进也卖不出。
            # dc 的因子选股回测（app/strategy/engine.py）用 trading_status.suspended 判，
            # 脚本回测拿不到那张表，用 volume<=0 近似 —— 停牌日不会有成交量。
            suspended = volumes is not None and i < len(volumes) and (
                not np.isfinite(volumes[i]) or volumes[i] <= 0
            )

            # 涨跌停按**前收**算 —— 这才是真实的封板价
            if limit_pct is not None and i > 0:
                prev_close = float(closes[i - 1])
                limit_up = round(prev_close * (1 + limit_pct), 2)
                limit_down = round(prev_close * (1 - limit_pct), 2)
            else:
                limit_up, limit_down = math.inf, 0.0

            for sig in by_bar.get(i, []):
                price = float(sig["price"])
                want = int(sig["quantity"])

                if suspended:
                    reject(i, sig["type"], price, want, "停牌 / 无成交，无法撮合")
                    continue

                if sig["type"] == "buy":
                    if price >= limit_up - 1e-9:
                        reject(i, "buy", price, want, "涨停封板，买单不成交")
                        continue

                    qty = (want // lot) * lot if lot > 1 else want
                    if qty <= 0:
                        reject(i, "buy", price, want, f"不足最小交易单位（{lot} 股）")
                        continue

                    # 连费用一起算进去，否则会出现「买得起但付不起手续费」的虚高成交
                    qty = self._affordable_qty(price, qty, cash, lot)
                    if qty <= 0:
                        reject(i, "buy", price, want, "可用资金不足（含交易费用）")
                        continue

                    amount = price * qty
                    fee = calc_fees("buy", amount, rules)
                    cash -= amount + fee
                    total_fees += fee
                    lots.append({"qty": qty, "price": price, "bar": i})
                    trades.append({
                        "type": "buy",
                        "price": float(price),
                        "quantity": int(qty),
                        "timestamp": index[i],   # 真实的 bar 时间，不是 now()
                        "bar": int(i),
                        "fee": round(float(fee), 4),
                    })

                else:
                    if price <= limit_down + 1e-9:
                        reject(i, "sell", price, want, "跌停封板，卖单不成交")
                        continue

                    # T+1：当日买入的批次不可卖
                    if t0:
                        available = sum(l["qty"] for l in lots)
                    else:
                        available = sum(l["qty"] for l in lots if l["bar"] < i)

                    if available <= 0:
                        reason = ("无可用持仓" if not lots
                                  else "T+1：当日买入的股份当日不可卖出")
                        reject(i, "sell", price, want, reason)
                        continue

                    qty = min(want, available)
                    if qty <= 0:
                        continue

                    remaining = qty
                    for l in lots:
                        if remaining <= 0:
                            break
                        if not t0 and l["bar"] >= i:
                            continue          # 今日买入，锁住
                        take = min(remaining, l["qty"])
                        l["qty"] -= take
                        remaining -= take
                    lots = [l for l in lots if l["qty"] > 0]

                    amount = price * qty
                    fee = calc_fees("sell", amount, rules)
                    cash += amount - fee
                    total_fees += fee
                    trades.append({
                        "type": "sell",
                        "price": float(price),
                        "quantity": int(qty),
                        "timestamp": index[i],
                        "bar": int(i),
                        "fee": round(float(fee), 4),
                    })

            close_i = float(closes[i])
            held = sum(l["qty"] for l in lots)
            portfolio_values.append(cash + held * close_i)

        self.trades = trades
        self.portfolio_values = portfolio_values
        self.daily_returns = self._calculate_daily_returns(portfolio_values)

        # 回测结果必须带着它的前提一起呈现，否则看到的只是一堆没有语境的数字
        warnings: List[str] = []
        if rules is None:
            warnings.append(
                "未套用任何市场规则：T+0、无涨跌停、无最小交易单位、零费用 —— "
                "结果偏乐观，不能直接用于 A 股决策"
            )
        else:
            warnings.append(
                f"市场规则：{rules.label} · "
                f"{'T+0' if rules.same_day_roundtrip else 'T+1'} · "
                f"{rules.lot_note} · {rules.fee_note}"
            )
            if rules.has_price_limit:
                warnings.append(
                    f"涨跌停：按前收 ±{(self._limit_pct or 0) * 100:g}% 判定，封板不成交"
                )
        warnings.append("撮合假设：信号在发出的当根 bar 按收盘价成交；未计滑点与冲击成本")
        warnings.append("停牌判定：成交量 <= 0 的 bar 视为停牌，当日不撮合")

        if self._infer_stats["price_matched"]:
            warnings.append(
                f"其中 {self._infer_stats['price_matched']} 笔信号的成交时间由收盘价匹配推断；"
                f"如需精确指定，写成 buy(price, qty, bar=i) / sell(price, qty, bar=i)"
            )

        # 被拒信号必须说出来 —— 否则报告描述的是另一个策略
        if rejections:
            by_reason: Dict[str, int] = {}
            for r in rejections:
                by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
            warnings.append(
                "被市场规则挡下的信号："
                + "；".join(f"{reason} × {cnt}" for reason, cnt in by_reason.items())
                + f"（共 {len(rejections)} 笔，未成交）"
            )

        return {
            "trades": trades,
            # final_capital 是可用的现金；总收益要看组合价值（现金 + 持仓市值）
            "final_capital": cash,
            "final_position": sum(l["qty"] for l in lots),
            "portfolio_values": portfolio_values,
            # 组合价值没有日期就画不出净值曲线 —— 前端拿不到 bar 的时间
            "portfolio_dates": [d.strftime("%Y-%m-%d") for d in pd.to_datetime(index)],
            "daily_returns": self.daily_returns,
            "rejections": rejections,
            "total_fees": round(float(total_fees), 4),
            "warnings": warnings,
        }

    def _affordable_qty(self, price: float, want: int, cash: float, lot: int) -> int:
        """在「成交额 + 费用 ≤ 可用资金」的前提下，返回最大可买数量。"""
        if price <= 0 or want <= 0 or cash <= 0:
            return 0

        qty = want
        step = lot if lot > 1 else max(1, qty // 1000)
        guard = 0
        while qty > 0 and guard < 10_000:
            amount = price * qty
            if amount + calc_fees("buy", amount, self.market) <= cash:
                return qty
            qty -= step
            guard += 1
        return max(0, qty)

    # ── 指标 ─────────────────────────────────────────────

    def calculate_metrics(self, results: Dict) -> Dict:
        """计算回测指标。"""
        trades = results["trades"]
        portfolio_values = results.get("portfolio_values") or []

        # 总收益按组合价值算：期末可能仍持仓，只看现金会低估
        if portfolio_values:
            ending_value = float(portfolio_values[-1])
            total_return = (ending_value - self.initial_capital) / self.initial_capital * 100
        else:
            ending_value = float(results.get("final_capital", self.initial_capital))
            total_return = (ending_value - self.initial_capital) / self.initial_capital * 100

        max_drawdown = self._calculate_max_drawdown(portfolio_values) if portfolio_values else 0
        sharpe_ratio = self._calculate_sharpe_ratio(results.get("daily_returns", []))
        win_rate = self._calculate_win_rate(trades)

        return {
            "total_return": round(total_return, 2),
            "sharpe_ratio": round(sharpe_ratio, 2),
            "max_drawdown": round(max_drawdown, 2),
            "win_rate": round(win_rate, 2),
            "total_trades": len(trades),
            "final_capital": round(ending_value, 2),
        }

    def _calculate_win_rate(self, trades: List[Dict]) -> float:
        """按 FIFO 配对算真实胜率：盈利的平仓次数 / 已完成的平仓次数。

        未平仓的部分不计入分母 —— 它还没有结果。
        """
        trips = self._round_trips(trades)
        if not trips:
            return 0.0
        wins = sum(1 for t in trips if t["pnl"] > 0)
        return wins / len(trips) * 100

    @staticmethod
    def _round_trips(trades: List[Dict]) -> List[Dict]:
        """FIFO 配对买卖，返回每笔完整平仓的盈亏。

        先进先出：卖单按买入的先后顺序依次平仓。一笔卖单若跨了多个买入批次，
        记为一个 round trip，entry_price 取各批次的加权均价，pnl 仍是准确的。
        未平仓的买入批次不产生 round trip。
        """
        lots: List[Dict] = []      # 未平仓的买入批次，队首最早
        trips: List[Dict] = []

        for t in trades:
            price = float(t["price"])
            qty = int(t["quantity"])
            if qty <= 0:
                continue

            if t["type"] == "buy":
                lots.append({"qty": qty, "price": price})
                continue

            remaining = qty
            cost = 0.0
            filled = 0
            while remaining > 0 and lots:
                lot = lots[0]
                take = min(remaining, lot["qty"])
                cost += take * lot["price"]
                filled += take
                remaining -= take
                lot["qty"] -= take
                if lot["qty"] <= 0:
                    lots.pop(0)

            if filled <= 0:
                continue

            entry = cost / filled
            trips.append({
                "entry_price": entry,
                "exit_price": price,
                "quantity": filled,
                "pnl": (price - entry) * filled,
                "pnl_pct": (price / entry - 1) * 100 if entry > 0 else 0.0,
            })

        return trips

    def _calculate_max_drawdown(self, portfolio_values: List[float]) -> float:
        """计算最大回撤（返回正数百分比）"""
        if not portfolio_values:
            return 0

        peak = portfolio_values[0]
        max_dd = 0.0

        for value in portfolio_values:
            if value > peak:
                peak = value
            if peak > 0:
                drawdown = (peak - value) / peak * 100
                if drawdown > max_dd:
                    max_dd = drawdown

        return max_dd

    def _calculate_sharpe_ratio(self, returns: List[float]) -> float:
        """计算年化夏普比率（无风险利率为 0）"""
        if not returns or len(returns) < 2:
            return 0.0

        returns_array = np.asarray(returns, dtype=float)
        returns_array = returns_array[np.isfinite(returns_array)]
        if len(returns_array) < 2:
            return 0.0

        std_return = returns_array.std()
        if std_return == 0 or not math.isfinite(std_return):
            return 0.0

        sharpe = (returns_array.mean() / std_return) * np.sqrt(252)
        return float(sharpe) if math.isfinite(sharpe) else 0.0

    def _calculate_daily_returns(self, portfolio_values: List[float]) -> List[float]:
        """计算每日收益率"""
        if len(portfolio_values) < 2:
            return []

        returns = []
        for i in range(1, len(portfolio_values)):
            prev = portfolio_values[i - 1]
            if prev and prev > 0:
                returns.append(float((portfolio_values[i] - prev) / prev))
            else:
                returns.append(0.0)

        return returns
