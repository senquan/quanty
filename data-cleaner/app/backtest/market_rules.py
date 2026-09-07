"""市场规则表 —— dc 端「这个市场能做什么、不能做什么」的唯一事实来源。

迁移自 ``backend/app/services/market_rules.py``（2026-09-06 P1 落地，80 项测试），
按 dc 的数据现实做了裁剪：**只保留 A 股**。

为什么删掉港股/美股/加密：
    那三套规则写在 backend，是因为 backend 挂在 yfinance / ccxt 上。
    dc 只有 A 股行情（``factor.raw_bars``），把港美股规则留在这里，
    等于宣称一个这里根本拿不到数据的能力 —— 闸口的诚实性会因此打折。

    dc 的闸口遇到非 A 股代码，应当明确说「dc 只有 A 股行情」，
    而不是给一份跑不出数据的 Plan。

费率口径（A股，2026 年现行）::

    佣金    万 2.5 双边，单笔最低 5 元
    过户费  万 0.1 双边（沪深均收）
    印花税  万 5 卖出单边

涨跌停判定口径（与 dc 因子选股回测保持一致）：
    dc 的 ``app/strategy/engine.py`` 用 ``trading_status.limit_up`` 字段判定封板；
    脚本回测拿不到那张表，改用**前收 ± 板块幅度**推算（与 backend 一致）。
    两条路径都是「封板则买不进/卖不出」，结论一致，只是精度不同 ——
    这一点会写进回测报告的 warnings，不藏着。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MarketRules:
    key: str
    label: str
    can_short: bool
    #: True = T+0，当日买入可以当日卖出
    same_day_roundtrip: bool
    #: 涨跌停说明；None = 无涨跌停
    price_limit_note: Optional[str]
    #: 最小交易单位；0 或 1 = 不限
    lot_size: int
    currency: str

    commission_rate: float = 0.0     # 佣金，双边
    commission_min: float = 0.0      # 佣金单笔最低
    transfer_fee_rate: float = 0.0   # 过户费，双边
    stamp_duty_rate: float = 0.0     # 印花税
    stamp_both_sides: bool = False   # 印花税是否双边收（A 股只卖出收）

    @property
    def has_price_limit(self) -> bool:
        return self.price_limit_note is not None

    @property
    def lot_note(self) -> str:
        if self.lot_size <= 1:
            return "支持碎股（可买非整数股）"
        return f"{self.lot_size} 股整手（不足一手只能卖不能买）"

    @property
    def fee_note(self) -> str:
        parts = []
        if self.commission_rate:
            parts.append(
                f"佣金万{self.commission_rate * 10000:g}"
                + (f"（最低 {self.commission_min:g} 元）" if self.commission_min else "")
            )
        if self.transfer_fee_rate:
            parts.append(f"过户费万{self.transfer_fee_rate * 10000:g} 双边")
        if self.stamp_duty_rate:
            sides = "双边" if self.stamp_both_sides else "卖出单边"
            parts.append(f"印花税万{self.stamp_duty_rate * 10000:g} {sides}")
        return " + ".join(parts) if parts else "零佣金（简化口径）"


MARKETS = {
    "a_share": MarketRules(
        key="a_share", label="A股",
        can_short=False, same_day_roundtrip=False,
        price_limit_note="主板 ±10% / 创业板·科创板 ±20% / 北交所 ±30%",
        lot_size=100, currency="CNY",
        commission_rate=0.00025, commission_min=5.0,
        transfer_fee_rate=0.00001, stamp_duty_rate=0.0005, stamp_both_sides=False,
    ),
}

#: 闸口认得的写法，用于报错时给出可执行的补救建议
SYMBOL_HINTS = "A股 600519.SH / 000001.SZ / 920808.BJ（纯数字 600519 也能认）"

#: dc 唯一的数据所在地
DATA_SOURCE = "factor.raw_bars"


def _digits(symbol: str) -> str:
    return re.sub(r"\D", "", symbol or "")[:6]


def normalize_symbol(symbol: str) -> Optional[str]:
    """把用户写法规范成库里的 ``600519.SH`` 形态；认不出返回 None。

    库里 5,555 只标的全部带后缀（SH/SZ/BJ），纯数字代码必须补全才能查库。
    """
    s = (symbol or "").strip().upper()
    if not s:
        return None
    m = re.fullmatch(r"(\d{6})\.(SH|SS|SZ|BJ)", s)
    if m:
        code, mkt = m.group(1), m.group(2)
        return f"{code}.{'SH' if mkt == 'SS' else mkt}"
    if re.fullmatch(r"\d{6}", s):
        d = s
        if d.startswith(("60", "68", "51", "58", "11")):
            return f"{d}.SH"
        if d.startswith(("00", "30", "12", "15", "16")):
            return f"{d}.SZ"
        if d.startswith(("8", "4", "92")):
            return f"{d}.BJ"
        return None
    return None


def classify_symbol(symbol: str) -> Optional[MarketRules]:
    """从代码认市场。认不出返回 None —— 由闸口转成一句说得清的拒绝。"""
    return MARKETS["a_share"] if normalize_symbol(symbol) else None


def a_share_limit_pct(symbol: str) -> float:
    """按板块返回 A 股涨跌停幅度。

    ST 股是 ±5%，但代码上看不出来（名称才有 ST 前缀），
    所以这里给的是板块默认值 —— 这条限制会写进报告的 limits 里。
    """
    d = _digits(symbol)
    if d.startswith(("300", "301")):                 # 创业板
        return 0.20
    if d.startswith(("688", "689")):                 # 科创板（689 = CDR，如九号公司）
        return 0.20
    if d.startswith(("8", "4", "92")):               # 北交所
        return 0.30
    return 0.10                                      # 主板 / 中小板


def calc_fees(side: str, amount: float, rules: Optional[MarketRules]) -> float:
    """按市场规则算一笔成交的交易费用。side 为 'buy' / 'sell'。"""
    if rules is None or amount <= 0:
        return 0.0

    commission = max(amount * rules.commission_rate, rules.commission_min)
    transfer = amount * rules.transfer_fee_rate
    stamp = amount * rules.stamp_duty_rate if (
        rules.stamp_both_sides or side == "sell"
    ) else 0.0
    return commission + transfer + stamp
