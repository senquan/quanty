"""AlphaFeed A 股日线行情数据源适配器

通过 AlphaFeed REST API 获取 A 股（沪深京）日线 K 线：
- 主路径 `GET /v1/klines` 取前复权（forward）收盘价。AlphaFeed 的前复权以最新一日为锚，
  故 close 即全局一致的前复权价（qfq），清洗流水线的 adjust 步骤（adj_close = close）
  天然得到真实前复权价，技术因子（如 RSI）可直接基于真实 A 股数据计算。
- 复权因子 `GET /v1/klines/ex-factors`（除权因子）全历史拉取，按全局 f_first/f_latest 归一化，
  写入 `adj_factor` 与 `hfq_close`，与 Tushare 接入层语义对齐
  （`hfq_close = close * f_latest / f_first`）。该端点请求失败时仅降级：
  `adj_factor`/`hfq_close` 置空，qfq close 不受影响。

认证：Header `X-API-Key`
代码格式：600519.SH / 000001.SZ
文档：https://docs.alphafeed.org/zh-Hans
依赖配置：ALPHAFEED_KEY（见 app.core.config.settings）
"""
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd

from app.core.config import settings
from app.core.exceptions import IngestionError
from app.core.logging import get_logger
from app.ingestion.base import BaseSource
from app.ingestion.schemas import RawBar

logger = get_logger(__name__)


class AlphafeedSource(BaseSource):
    name = "alphafeed"

    # 本服务仅支持日线（A 股因子多为日频）
    _SUPPORTED_FREQ = {"1d"}

    def _key(self) -> str:
        key = getattr(settings, "ALPHAFEED_KEY", None)
        if not key:
            raise IngestionError("缺少 ALPHAFEED_KEY，无法访问 AlphaFeed")
        return key

    @staticmethod
    def _to_ms(d: str, end_of_day: bool = False) -> int:
        """将 '2020-01-01' 转为毫秒时间戳（Asia/Shanghai）。

        A 股日线 bar 的时间戳是北京时间零点。若按 UTC 解析会相差 8 小时，
        把当天的 bar 挤出 [start, end] 区间（表现为"最新一天永远拉不到"）。
        因此统一按 UTC+8 解析；end 取当日 23:59:59.999 保证闭区间语义。
        """
        cst = timezone(timedelta(hours=8))
        dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=cst)
        if end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59, microsecond=999000)
        return int(dt.timestamp() * 1000)

    # 复权因子全历史起点（与 Tushare 对齐，保证 f_first/f_latest 为全局归一化基准）
    _EX_FACTOR_START = "1990-01-01"

    def _request_json(self, url: str, params: dict, headers: dict) -> dict:
        """统一发 REST GET 并处理 401/403/429/非 200，返回解析后的 JSON payload。"""
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(url, params=params, headers=headers)
        except httpx.HTTPError as e:
            raise IngestionError(f"AlphaFeed 请求失败({url}): {e}") from e

        if resp.status_code == 401:
            raise IngestionError("AlphaFeed 认证失败：ALPHAFEED_KEY 无效或缺失")
        if resp.status_code == 403:
            raise IngestionError("AlphaFeed 无权限：套餐不包含该市场/功能")
        if resp.status_code == 429:
            retry = resp.json().get("retry_after_ms", 1000)
            raise IngestionError(f"AlphaFeed 触发限频，请 {retry}ms 后重试")
        if resp.status_code != 200:
            raise IngestionError(
                f"AlphaFeed 返回 {resp.status_code}: {resp.text[:200]}"
            )
        try:
            return resp.json()
        except ValueError as e:
            raise IngestionError(f"AlphaFeed 响应非 JSON: {e}") from e

    def _get_ex_factors(
        self, base: str, headers: dict, symbol: str, end: str
    ) -> tuple[pd.Series | None, float, float]:
        """拉取除权因子（复权因子）。

        返回 (ex_series, f_latest, f_first)：
        - ex_series：按 bar 日期 ffill 查找的因子序列（datetime 索引，naive，与 bar 同一解析方式）。
        - f_latest / f_first：全历史最大 / 最小因子，用于 hfq 归一化。
        任意失败返回 (None, 1.0, 1.0)，调用方据此将 adj_factor / hfq_close 置空（降级）。
        """
        url = f"{base.rstrip('/')}/v1/klines/ex-factors"
        params = {
            "symbols": symbol,
            "start_time": self._to_ms(self._EX_FACTOR_START),
            "end_time": self._to_ms(end, end_of_day=True),
        }
        try:
            payload = self._request_json(url, params, headers)
        except IngestionError as e:
            logger.warning(
                "AlphaFeed ex_factors 获取失败（降级：adj_factor/hfq_close 置空）",
                extra={"task": "ingest", "symbol": symbol, "reason": str(e)[:120]},
            )
            return None, 1.0, 1.0

        data = payload.get("data") if isinstance(payload, dict) else None
        entries = data.get(symbol) if isinstance(data, dict) else None
        if not entries:
            return None, 1.0, 1.0

        ts_list, f_list = [], []
        for e in entries:
            ts_list.append(datetime.fromtimestamp(int(e["timestamp"]) / 1000))
            f_list.append(float(e["ex_factor"]))
        ex_series = pd.Series(f_list, index=pd.DatetimeIndex(ts_list)).sort_index()
        return ex_series, float(ex_series.max()), float(ex_series.min())

    def fetch(
        self,
        symbol: str,
        start: str,
        end: str,
        freq: str = "1d",
    ) -> pd.DataFrame:
        if freq not in self._SUPPORTED_FREQ:
            raise IngestionError(f"AlphaFeed 行情源暂仅支持日线(1d)，收到: {freq}")

        key = self._key()
        base = getattr(settings, "ALPHAFEED_BASE_URL", "https://api.alphafeed.org")
        headers = {"X-API-Key": key}

        # 1) 主路径：前复权日线（API 侧以最新一日为锚，全局一致 qfq）
        klines = self._request_json(
            f"{base.rstrip('/')}/v1/klines",
            {
                "symbol": symbol,
                "period": "1d",
                "start_time": self._to_ms(start),
                "end_time": self._to_ms(end, end_of_day=True),
                "adjust": "forward",  # 前复权，close 即全局一致前复权价
            },
            headers,
        )
        data = klines.get("data") if isinstance(klines, dict) else None
        if not data or not data.get("timestamp"):
            # 停牌 / 退市 / 区间内无交易日属正常情况，返回空结果而非抛错，
            # 否则每日增量会把停牌股统计成 error，淹没真实的网络失败
            logger.info(
                "AlphaFeed 区间内无数据",
                extra={
                    "task": "ingest",
                    "symbol": symbol,
                    "start": start,
                    "end": end,
                },
            )
            return self._to_dataframe([])

        # 2) 复权因子（全历史；失败仅降级，qfq close 不受影响）
        ex_series, f_latest, f_first = self._get_ex_factors(base, headers, symbol, end)
        have_factor = ex_series is not None and f_first

        n = len(data["timestamp"])
        rows: list[RawBar] = []
        for i in range(n):
            ts_ms = int(data["timestamp"][i])
            # 转为 naive datetime（与 tushare 等源一致，pandera schema 要求 datetime64[ns]）
            dt = datetime.fromtimestamp(ts_ms / 1000)
            o, h, l, c = (
                float(data["open"][i]),
                float(data["high"][i]),
                float(data["low"][i]),
                float(data["close"][i]),
            )
            if h < l or c <= 0 or o <= 0:
                continue  # 剔除基础异常行
            adj_factor = None
            hfq_close = None
            if have_factor:
                # 该 bar 日期对应的复权因子：取不晚于 bar 的最近一次除权因子（ffill）
                past = ex_series[ex_series.index <= dt]
                f = float(past.iloc[-1]) if not past.empty else float(ex_series.iloc[0])
                adj_factor = f
                # 与 Tushare 对齐：hfq_close = qfq_close * (f_latest / f_first)
                hfq_close = c * (f_latest / f_first)
            rows.append(
                RawBar(
                    symbol=symbol,
                    timestamp=dt,
                    open=o,
                    high=h,
                    low=l,
                    close=c,  # 前复权收盘价（AlphaFeed 已前复权，全局基准一致）
                    volume=float(data.get("volume", [0] * n)[i] or 0),
                    source=self.name,
                    freq=freq,
                    adj_factor=adj_factor,
                    hfq_close=hfq_close,
                )
            )

        if not rows:
            logger.warning(
                "AlphaFeed 返回数据全部被质量过滤剔除",
                extra={"task": "ingest", "symbol": symbol},
            )
            return self._to_dataframe([])

        logger.info(
            "AlphaFeed 拉取完成",
            extra={"task": "ingest", "symbol": symbol, "rows": len(rows)},
        )
        return self._to_dataframe(rows)
