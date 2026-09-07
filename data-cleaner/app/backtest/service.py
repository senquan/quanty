"""脚本策略回测的编排层 —— 校验 → 闸口 → 取数 → 撮合 → 指标。

这是 dc 侧脚本策略回测的**唯一入口**（R3 的 HTTP 接口也只是它的薄壳）。
把编排独立出来，是因为「跑一次回测」要串起五件事，
任何一件散落在 API 层，都会在某个调用路径上被漏掉 ——
尤其是闸口：**漏掉闸口的回测照样能算出夏普和最大回撤，看不出任何异常**。

编排顺序（顺序本身有含义）::

    validate_code  语法/危险调用 —— 最便宜的先做
        ↓
    plan_backtest  闸口（取数前）：代码认不认得出、口径要不要分钟线、本金够不够一手
        ↓
    load_bars      dc 本地读 factor.raw_bars，定复权口径
        ↓
    check_sample   闸口（取数后）：真实 bar 数够不够这个口径
        ↓
    engine         两遍执行 + 逐 bar 撮合
        ↓
    metrics        收益 / 夏普 / 回撤 / 胜率

⚠️ 策略代码在本进程内 ``exec``（与 backend 同）。AST 校验挡的是误伤级别的错误，
不是恶意代码。dc 只应对内暴露，真要做租户隔离得另起沙箱进程。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from app.backtest.data import BacktestDataError, load_bars
from app.backtest.engine import BacktestEngine
from app.backtest.gate import Plan, Refusal, check_sample, plan_backtest
from app.backtest.market_rules import MARKETS
from app.backtest.validator import StrategyValidator


class BacktestRefused(Exception):
    """闸口拒绝了这个回测。带 ``refusal``，供 API 层直接转成 422。"""

    def __init__(self, refusal: Refusal) -> None:
        super().__init__(refusal.reason)
        self.refusal = refusal

    def as_dict(self) -> Dict[str, str]:
        return self.refusal.as_dict()


@dataclass
class ScriptBacktestRequest:
    symbol: str
    start: str
    end: str
    code: str
    style: str = "swing"
    initial_capital: float = 100_000.0
    allow_short: bool = False
    price_field: str = "qfq"
    #: False = 不套市场规则（T+0 / 无涨跌停 / 零费用），仅用于纯算法验证
    apply_market_rules: bool = True
    #: 可选：注入取数函数，签名同 RawBarRepository.load(symbol, start, end)
    loader: Optional[Callable[..., pd.DataFrame]] = field(default=None, repr=False)


def run_script_backtest(req: ScriptBacktestRequest) -> Dict[str, Any]:
    """跑一次脚本策略回测，返回结果字典（JSON 可序列化）。

    抛 :class:`BacktestRefused`（闸口拦下）或 :class:`BacktestDataError`（取数失败）。
    """
    # ① 代码校验 —— 语法错、禁止调用，先在这里挡掉，别等到 exec 才炸
    validation = StrategyValidator.validate_strategy(req.code)
    if not validation["valid"]:
        raise BacktestRefused(Refusal(
            reason="策略代码校验未通过：" + "；".join(validation["errors"]),
            remedy="按上面的提示改好代码再跑",
        ))

    # ② 闸口（取数前）
    plan = plan_backtest(
        symbol=req.symbol,
        start=req.start,
        end=req.end,
        style=req.style,
        initial_capital=req.initial_capital,
        allow_short=req.allow_short,
        price_field=req.price_field,
    )
    if isinstance(plan, Refusal):
        raise BacktestRefused(plan)

    # ③ 取数
    df, meta, data_warnings = load_bars(
        symbol=plan.symbol,
        start=plan.start,
        end=plan.end,
        price_field=plan.price_field,
        loader=req.loader,
    )

    # ④ 闸口（取数后）：用真实 bar 数复核样本
    refusal = check_sample(plan, int(meta["bars"]))
    if refusal is not None:
        raise BacktestRefused(refusal)

    # ⑤ 撮合
    market = plan.market if req.apply_market_rules else None
    cfg = plan.to_engine_config()

    # 后复权下价格被放大了 k 倍，本金不跟着放大就会「一手都买不起」 ——
    # 茅台（k≈8.88）实测：13 笔买单全部因不足一手被拒，成交 0 笔。
    # 名义价 + 名义本金 = 与真实价 + 真实本金**购买力完全等价**，
    # 整手判定因此一致；只有「佣金最低 5 元」这类绝对阈值会缩水成 5/k 元（量级可忽略）。
    k = float(meta.get("hfq_factor") or 1.0)
    scale = k if (plan.price_field == "hfq" and k > 0) else 1.0

    engine = BacktestEngine(
        initial_capital=plan.initial_capital * scale,
        market=market,
        symbol=cfg["symbol"],
        allow_short=cfg["allow_short"],
    )
    try:
        raw = engine.execute_strategy(req.code, df)
    except ValueError as exc:
        # 策略跑挂了：这是「这个策略跑不了」，不是服务端出错
        raise BacktestRefused(Refusal(
            reason=f"策略执行失败：{exc}",
            remedy="检查策略里的数据访问（列名、下标、除零），改好再跑",
        )) from exc

    # 比率类指标（收益率 / 夏普 / 回撤 / 胜率）在等比缩放下不变，先算再还原金额
    metrics = engine.calculate_metrics(raw)
    if scale != 1.0:
        raw = _restore_price_scale(raw, scale)
        metrics["final_capital"] = round(metrics["final_capital"] / scale, 2)

    trades = [
        {
            "type": t["type"],
            "price": float(t["price"]),
            "quantity": int(t["quantity"]),
            "timestamp": pd.to_datetime(t["timestamp"]).strftime("%Y-%m-%d"),
            "bar": int(t["bar"]),
            "fee": float(t.get("fee", 0.0)),
        }
        for t in raw["trades"]
    ]

    warnings: List[str] = list(data_warnings) + list(raw.get("warnings", []))
    if validation["warnings"]:
        warnings.extend(f"策略代码：{w}" for w in validation["warnings"])
    if not req.apply_market_rules:
        warnings.append(
            "调用方显式关闭了市场规则（apply_market_rules=false）—— "
            "结果不是 A 股可执行的结果"
        )

    return {
        "symbol": plan.symbol,
        "start": plan.start,
        "end": plan.end,
        "total_return": metrics["total_return"],
        "sharpe_ratio": metrics["sharpe_ratio"],
        "max_drawdown": metrics["max_drawdown"],
        "win_rate": metrics["win_rate"],
        "total_trades": metrics["total_trades"],
        "final_capital": metrics["final_capital"],
        "final_position": int(raw.get("final_position", 0)),
        "cash": round(float(raw.get("final_capital", 0.0)), 2),
        "trades": trades,
        "daily_returns": raw.get("daily_returns", []),
        "portfolio_values": raw.get("portfolio_values", []),
        "portfolio_dates": raw.get("portfolio_dates", []),
        # 限制与警告必须跟数字一起回去 —— 分开看等于没看
        "limits": list(plan.limits),
        "warnings": warnings,
        "rejections": raw.get("rejections", []),
        "total_fees": float(raw.get("total_fees", 0.0)),
        "plan": plan.as_dict(),
        "data": meta,
    }


def _restore_price_scale(raw: Dict[str, Any], k: float) -> Dict[str, Any]:
    """把 hfq 名义体系下的**绝对金额**还原成真实价，便于人读与横向比较。

    只动金额类字段；比率类（收益率 / 夏普 / 回撤 / 胜率）等比缩放下不变。
    """
    if not k or k == 1.0:
        return raw
    out = dict(raw)
    out["portfolio_values"] = [float(v) / k for v in raw.get("portfolio_values", [])]
    out["final_capital"] = float(raw.get("final_capital", 0.0)) / k     # 剩余现金
    out["total_fees"] = float(raw.get("total_fees", 0.0)) / k
    out["trades"] = [
        {**t, "price": float(t["price"]) / k, "fee": float(t.get("fee", 0.0)) / k}
        for t in raw.get("trades", [])
    ]
    out["rejections"] = [
        {**r, "price": float(r["price"]) / k} for r in raw.get("rejections", [])
    ]
    return out


def run_script_backtest_dict(payload: Dict[str, Any], loader=None) -> Dict[str, Any]:
    """字典版入口（供 HTTP / 脚本直接调用）。未提供的字段取默认值。"""
    return run_script_backtest(ScriptBacktestRequest(
        symbol=payload.get("symbol") or "",
        start=payload.get("start") or payload.get("start_date") or "",
        end=payload.get("end") or payload.get("end_date") or "",
        code=payload.get("code") or "",
        style=payload.get("style") or "swing",
        initial_capital=float(payload.get("initial_capital") or 100_000.0),
        allow_short=bool(payload.get("allow_short", False)),
        price_field=payload.get("price_field") or "qfq",
        apply_market_rules=bool(payload.get("apply_market_rules", True)),
        loader=payload.get("loader") or loader,
    ))
