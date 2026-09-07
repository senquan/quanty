"""回测闸口 —— 在跑之前先判「这个回测成不成立」。

迁移自 ``backend/app/services/backtest_gate.py``（2026-09-06 P1 落地）。
三问，缺一不可::

    ① 要回测什么？  代码 → 市场 → 引擎与市场规则
    ② 需要什么？    口径（长线/短线）→ 最少多少根 bar
    ③ 限制是什么？  这个市场 + 这个口径下，哪些事根本做不了

🔴 **闸口的价值在「拦住」，不在「放行」。**
   一个不成立的回测照样能算出夏普和最大回撤，数字排版整齐、看不出任何异常 ——
   而它测的东西压根不存在（用 60 根 bar 说三年胜率、在 A 股回测做空）。
   **能算出数字不等于这个数字有意义**，这一层就是拦这个的。

⇒ 闸口只输出两种东西：一个能跑的 :class:`Plan`，或一句说得清的 :class:`Refusal`。
  不输出「带着一堆警告勉强跑」的第三种 —— 警告没人看，数字人人看。

dc 侧的两处裁剪

1. **没有数据源选单**。backend 有 yahoo / crypto 两个源，要判「源和市场对不对得上」；
   dc 只有一个源 —— ``factor.raw_bars``，A 股。换来的是一条更硬的拒绝：
   非 A 股代码直接说「dc 没有这个市场的行情」。
2. **样本判定分两段**。先用日期区间粗估（能在取数前就拦掉明显不够的），
   取到数之后再用**真实 bar 数**复核（:func:`check_sample`）——
   区间的日历天数 ≠ 实际交易 bar 数，长期停牌的标的能差出一大截。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Union

from app.backtest.market_rules import (
    DATA_SOURCE,
    MARKETS,
    SYMBOL_HINTS,
    MarketRules,
    a_share_limit_pct,
    classify_symbol,
    normalize_symbol,
)


# ── 口径表：这是「需要什么」的事实来源 ──────────────────

@dataclass(frozen=True)
class Style:
    key: str
    label: str
    holding: str
    interval: str
    min_bars: int
    why_min: str


STYLES: Dict[str, Style] = {
    "long": Style(
        key="long", label="长线", holding="持仓以月 / 季度计", interval="1d",
        min_bars=480,
        why_min="持仓周期以月计时，两年才够十来个完整的进出；样本再少，"
                "胜率与盈亏比就是几笔交易的偶然",
    ),
    "swing": Style(
        key="swing", label="短线 / 波段", holding="持仓以天 / 周计", interval="1d",
        min_bars=240,
        why_min="持仓以周计时，一年约能形成几十笔交易，统计量才开始有意义",
    ),
    "intraday": Style(
        key="intraday", label="做 T（日内回转）", holding="当日进出", interval="1m",
        min_bars=0, why_min="",
    ),
}

#: 复权口径。回测用哪条价格序列，是结果可复现与否的前提。
PRICE_FIELDS = ("qfq", "hfq")

PRICE_FIELD_NOTE = {
    "qfq": "前复权（raw_bars.close）—— 名义价即真实价，整手 / 最低佣金 / 涨跌停判定都对；"
           "但锚在最新日，标的每次除权后历史值都会变，跨时间跑结果会漂",
    "hfq": "后复权（hfq_close 反推）—— 锚在最早日，历史值永不改变，结果可复现；"
           "但名义价不是真实价，整手与最低 5 元佣金按名义金额判定，与真实下单有偏差",
}


# ── 闸口的两种输出 ─────────────────────────────────────

@dataclass(frozen=True)
class Refusal:
    """这个回测不成立。``reason`` 说为什么，``remedy`` 说怎么才能跑。"""

    reason: str
    remedy: str

    def __bool__(self) -> bool:      # 便于 `if not plan:` 这样用
        return False

    def as_dict(self) -> Dict[str, str]:
        return {"reason": self.reason, "remedy": self.remedy}


@dataclass(frozen=True)
class Plan:
    """一个能跑的回测。``limits`` 是**必须随结果一起呈现**的限制说明。"""

    symbol: str
    market: MarketRules
    style: Style
    start: str
    end: str
    interval: str
    initial_capital: float
    data_source: str
    price_field: str
    #: 该板块的涨跌停幅度（A 股才有；None = 无涨跌停）
    limit_pct: Optional[float] = None
    limits: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    #: 调用方是否声明了要做空。A 股下这一位只能是 False（闸口已拦），
    #: 保留在 Plan 里是为了让报告能原样声明「本回测未启用做空」。
    allow_short: bool = False

    def __bool__(self) -> bool:
        return True

    def to_engine_config(self) -> Dict[str, Any]:
        """转成 :class:`~app.backtest.engine.BacktestEngine` 认得的构造参数。

        只放引擎真正认得的键 —— 多放一个，``BacktestEngine(**config)`` 就是一次 TypeError。
        """
        return {
            "symbol": self.symbol,
            "market": self.market,
            "allow_short": self.allow_short,
        }

    def as_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "market": self.market.key,
            "market_label": self.market.label,
            "style": self.style.key,
            "style_label": self.style.label,
            "start": self.start,
            "end": self.end,
            "interval": self.interval,
            "initial_capital": self.initial_capital,
            "data_source": self.data_source,
            "price_field": self.price_field,
            "limit_pct": self.limit_pct,
            "allow_short": self.allow_short,
            "limits": list(self.limits),
            "notes": list(self.notes),
        }


# ── 闸口本体 ───────────────────────────────────────────

def _parse_day(value: Any, what: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError as exc:
        raise ValueError(f"{what} 要写成 YYYY-MM-DD，收到 {value!r}") from exc


def _trading_days(start: date, end: date) -> int:
    """粗估区间内的交易日数（按每周 5 天，不扣长假 —— 只用来判「够不够」，宁可高估）。"""
    return max(int((end - start).days * 5 / 7), 0)


def plan_backtest(
    symbol: str,
    start: Any,
    end: Any,
    style: str = "swing",
    initial_capital: float = 100_000.0,
    allow_short: bool = False,
    price_field: str = "qfq",
    interval: str = "1d",
) -> Union["Plan", "Refusal"]:
    """闸口本体（取数前）。返回能跑的 :class:`Plan`，或说得清的 :class:`Refusal`。"""

    # ① 要回测什么 —— 代码认得出吗、属于哪个市场
    if not symbol or not str(symbol).strip():
        return Refusal("没给标的代码", f"给一个代码，如 {SYMBOL_HINTS}")

    norm = normalize_symbol(symbol)
    rules = classify_symbol(symbol)
    if rules is None or norm is None:
        return Refusal(
            f"认不出 {symbol!r} 是哪个 A 股标的代码",
            f"按这几种写法之一给：{SYMBOL_HINTS}。"
            f"dc 只有 A 股行情（factor.raw_bars），港美股 / 加密请走 backend",
        )

    # ② 需要什么 —— 口径决定 bar 粒度与最少样本
    st = STYLES.get(style)
    if st is None:
        return Refusal(
            f"不认识的口径 {style!r}",
            "用 long（长线）/ swing（短线波段）/ intraday（做T）之一",
        )

    pf = (price_field or "qfq").lower()
    if pf not in PRICE_FIELDS:
        return Refusal(
            f"不认识的复权口径 {price_field!r}",
            f"用 {' / '.join(PRICE_FIELDS)} 之一",
        )

    try:
        d0, d1 = _parse_day(start, "start_date"), _parse_day(end, "end_date")
    except ValueError as exc:
        return Refusal(str(exc), "改成 YYYY-MM-DD")

    if d0 >= d1:
        return Refusal(f"起始日期不早于结束日期（{d0} → {d1}）", "把区间调过来")

    # ③ 限制是什么 —— 先看有没有直接不成立的
    if st.key == "intraday":
        return Refusal(
            "做T 要分钟级 bar，而 dc 只有日线（factor.raw_bars，freq='1d'） —— "
            "拿日线跑日内策略，出来的数字看着完全正常，但它测的压根不是日内",
            "改用 swing（短线波段）看多日持仓的表现；做T 要先接分钟级数据",
        )
    if interval not in ("1d", "1D", "day", "daily"):
        return Refusal(
            f"当前只支持日线 bar，收到 interval={interval!r}",
            "用 '1d'，或等分钟级数据接进来",
        )

    cash = float(initial_capital) if isinstance(initial_capital, (int, float)) else float("nan")
    if not (cash > 0) or not math.isfinite(cash):
        return Refusal(
            f"起始资金要是一个大于 0 的有限数字，收到 {initial_capital!r}",
            "给个正常的数，比如 100000",
        )
    # 一手都买不起时算不出任何东西，但引擎照样会输出一份「总收益 0.00%」的完整报告
    if rules.lot_size > 1 and cash < 10_000:
        return Refusal(
            f"起始资金 {cash:,.0f} 太小 —— {rules.label}最小交易单位是{rules.lot_note}，"
            f"很可能一手都买不起，回测会得到一份「零成交、总收益 0.00%」的报告，"
            f"看不出是资金不够",
            "把起始资金调到能买得起至少一手的水平",
        )

    est = _trading_days(d0, d1)
    if est < st.min_bars:
        return Refusal(
            f"{st.label}至少要约 {st.min_bars} 根日线，这个区间大约只有 {est} 根。"
            f"{st.why_min}",
            f"把区间拉长到约 {round(st.min_bars * 7 / 5 / 365, 1)} 年以上，"
            f"或改用更短的口径",
        )

    if allow_short and not rules.can_short:
        return Refusal(
            f"{rules.label}不能做空，引擎会把做空信号直接拒掉 —— "
            f"带做空的策略在这里回测出来的是「只做多」的结果，不是你写的那个策略",
            "把策略改成只做多，或换到支持做空的市场",
        )

    limit_pct = a_share_limit_pct(norm) if rules.has_price_limit else None

    limits = [
        f"交易机制：{'T+0，当日可回转' if rules.same_day_roundtrip else 'T+1，当日买入次日才能卖'}",
        f"做空：{'允许' if rules.can_short else '不允许（做空信号会被拒掉）'}",
        f"最小交易单位：{rules.lot_note}",
        f"费用：{rules.fee_note}",
        f"计价币种：{rules.currency}",
    ]
    if limit_pct is not None:
        limits.append(f"涨跌停：{rules.price_limit_note}（按前收 ±{limit_pct * 100:g}% 推算，封板不成交）")

    notes = [
        f"口径：{st.label}（{st.holding}）",
        f"bar 粒度：日线；区间约 {est} 根（取数后按实际 bar 数复核）",
        f"复权口径：{PRICE_FIELD_NOTE[pf]}",
        "信号在发出的当根 bar 按收盘价成交（不是次日开盘 —— 这偏乐观）",
    ]

    return Plan(
        symbol=norm, market=rules, style=st,
        start=str(d0), end=str(d1),
        interval=st.interval, initial_capital=cash,
        data_source=DATA_SOURCE, price_field=pf,
        limit_pct=limit_pct,
        limits=limits, notes=notes, allow_short=bool(allow_short),
    )


def check_sample(plan: "Plan", n_bars: int) -> Optional[Refusal]:
    """取数后复核：真实 bar 数够不够这个口径。

    日期区间只能粗估 —— 长期停牌、次新股、退市整理期都会让实际 bar 数远少于日历天数。
    这一段拿到真实数据，就不该再用估算值放行。
    """
    if n_bars <= 0:
        return Refusal(
            f"{plan.symbol} 在 {plan.start} ~ {plan.end} 没有行情数据"
            f"（数据源 {plan.data_source}）",
            "换个区间，或先在 dc 补这个标的的历史行情",
        )
    if n_bars < plan.style.min_bars:
        return Refusal(
            f"{plan.style.label}至少要约 {plan.style.min_bars} 根日线，"
            f"{plan.symbol} 在区间内实际只有 {n_bars} 根。{plan.style.why_min}",
            f"把区间拉长到约 {round(plan.style.min_bars * 7 / 5 / 365, 1)} 年以上，"
            f"或改用更短的口径",
        )
    return None
