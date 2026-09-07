"""回测取数层 —— 从 ``factor.raw_bars`` 取出一根标的的日线，并定好复权口径。

这一层只做三件事，**不做任何策略语义**：

1. 查库（``RawBarRepository.load``），拿不到就是拿不到，不静默补零；
2. 定复权口径（qfq / hfq），并把 OHLC 一起按同一比例缩放 ——
   只缩放 close 会让 ``high/low`` 与 close 不在同一把尺子上，
   而 ATR / 随机指标 / 布林带都是拿 high/low 算的，**序列一旦不自洽，指标就是错的**；
3. 把结果说清楚：实际取到几根、覆盖区间、hfq 的换算系数 k 是多少、
   有没有因为缺 hfq 而退回 qfq。

复权现状（2026-09-06 R1a / R1b 之后，见 ``docs/memo/architecture.md`` §6.2 §6.3）：

- ``close`` = **前复权 qfq**（R1a 全量重拉，全库假跳空 0 条）
- ``hfq_close`` = **后复权 hfq**（R1b 补齐，5,555/5,555 覆盖 100%）
- 两者相差一个**每标的常数** k = hfq_close / close

k 是常数，所以两条序列的**收益率完全相同**；差别只在与**绝对价格**绑定的规则上：
整手（100 股）、佣金最低 5 元、起始资金够不够买一手。
⇒ qfq 判定准（真实价），hfq 可复现（历史值不变）。默认 qfq，理由写进 gate 的 notes。
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_OHLC = ("open", "high", "low", "close")


class BacktestDataError(Exception):
    """回测取数失败。带 ``reason`` / ``remedy``，供 API 层直接转成 422。"""

    def __init__(self, reason: str, remedy: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.remedy = remedy

    def as_dict(self) -> Dict[str, str]:
        return {"reason": self.reason, "remedy": self.remedy}


def _default_loader():
    from app.storage.raw_store import repository

    return repository


def _hfq_factor(df: pd.DataFrame) -> Optional[float]:
    """该标的的 hfq/qfq 换算系数 k（应当是一个常数，取中位数抗噪）。"""
    if "hfq_close" not in df.columns:
        return None
    hfq = pd.to_numeric(df["hfq_close"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    ratio = (hfq / close).replace([np.inf, -np.inf], np.nan).dropna()
    ratio = ratio[ratio > 0]
    if ratio.empty:
        return None
    k = float(ratio.median())
    if not np.isfinite(k) or k <= 0:
        return None
    return k


def load_bars(
    symbol: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    price_field: str = "qfq",
    loader: Optional[Callable[..., pd.DataFrame]] = None,
) -> Tuple[pd.DataFrame, Dict[str, object], List[str]]:
    """取一根标的的日线，返回 ``(df, meta, warnings)``。

    ``df`` 以 timestamp 为 DatetimeIndex（朴素北京时间），列为
    open / high / low / close / volume（+ amount、adj_factor 若库里有）。

    ``loader`` 可注入，签名同 ``RawBarRepository.load(symbol, start, end)`` ——
    测试时不必连库。
    """
    warnings: List[str] = []
    load = loader or _default_loader().load

    try:
        raw = load(symbol, start, end)
    except Exception as exc:  # noqa: BLE001
        raise BacktestDataError(
            f"读取 {symbol} 行情失败：{exc}",
            "检查 dc 与 factor_db 的连通，或稍后重试",
        ) from exc

    if raw is None or len(raw) == 0:
        raise BacktestDataError(
            f"{symbol} 在 {start or '起始'} ~ {end or '结束'} 没有行情数据",
            "换个区间，或先在 dc 补这个标的的历史行情"
            f"（数据源 factor.raw_bars，freq='1d'）",
        )

    df = raw.copy()
    if "timestamp" not in df.columns:
        raise BacktestDataError(
            f"行情缺少 timestamp 列（实际列：{list(df.columns)}）",
            "检查 raw_bars schema",
        )

    df["timestamp"] = pd.to_datetime(df["timestamp"])
    if str(df["timestamp"].dtype).endswith(", UTC]"):
        df["timestamp"] = df["timestamp"].dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
    df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
    df = df.set_index("timestamp")

    for col in (*_OHLC, "volume"):
        if col not in df.columns:
            raise BacktestDataError(f"行情缺少 {col} 列", "检查 raw_bars schema")
        df[col] = pd.to_numeric(df[col], errors="coerce")

    n_total = len(df)
    # 收盘价缺失的 bar 参与不了撮合，也画不出净值 —— 直接丢掉比让它变成 0 安全
    before = len(df)
    df = df[df["close"].notna() & (df["close"] > 0)]
    if len(df) < before:
        warnings.append(f"剔除了 {before - len(df)} 根收盘价缺失/非正的 bar")

    if df.empty:
        raise BacktestDataError(
            f"{symbol} 在 {start or '起始'} ~ {end or '结束'} 的收盘价全部缺失",
            "换个区间，或在 dc 重拉这个标的的行情",
        )

    # ── 复权口径 ──
    pf = (price_field or "qfq").lower()
    k: Optional[float] = None
    if pf == "hfq":
        k = _hfq_factor(df)
        if k is None:
            warnings.append(
                "缺 hfq_close（或无效），已退回前复权 qfq —— "
                "结果仍可用，但历史值会随后续除权漂移"
            )
            pf = "qfq"
        else:
            for col in _OHLC:
                df[col] = df[col] * k
            warnings.append(
                f"后复权：OHLC 统一 ×{k:.6g}（hfq_close/close 中位数）。"
                f"本金按同一系数放大后再撮合（购买力等价），报告金额已还原为真实价；"
                f"仅「佣金最低 5 元」这类绝对阈值在名义体系下等价于 {5 / k:.4g} 元"
            )

    first_day = df.index[0]
    last_day = df.index[-1]
    meta: Dict[str, object] = {
        "symbol": symbol,
        "price_field": pf,
        "requested_price_field": (price_field or "qfq").lower(),
        "hfq_factor": k,
        "bars": int(len(df)),
        "bars_raw": int(n_total),
        "first_date": first_day.strftime("%Y-%m-%d"),
        "last_date": last_day.strftime("%Y-%m-%d"),
        "source": (str(df["source"].iloc[-1]) if "source" in df.columns else None),
    }

    return df, meta, warnings
