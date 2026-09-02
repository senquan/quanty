"""东方财富妙想券商适配器（实盘）

协议参考 `docs/repos/mx-trader-bridge/trader.py`：
    POST {MX_API_URL}/api/claw/mockTrading/{trade|balance|positions|orders|cancel}
    header: {"apikey": MX_APIKEY}
    响应:   {"code":"200","data":{"rc":0,"rmsg":"","orderID":"...","result":{...}}}

要点（务必保留，否则会被"假成功"欺骗）：
- **顶层 code != "200" 直接判失败**（如余额不足时 data 可能为 null）。
- **rc 取 data.rc**（不是 data.result.rc），rc != 0 即失败。
- **下单成功 ≠ 成交**：需 `verify_filled` 轮询委托，`status==4` 且 tradeCount 达标才算成交；
  `status==8` 为已撤。

默认 `dry_run=True`：不发起任何真实请求，仅返回一份模拟成交结果，用于打通链路与联调。
关闭 dry-run 前必须确认：凭证已配置、已获 API 权限、理解上述成交双校验语义。
"""
import time
from datetime import datetime
from typing import Any
from uuid import uuid4

import httpx

from app.core.config import settings
from app.models.trading import MODE_LIVE
from app.services.broker.base import BrokerAdapter
from app.services.huatai_trading import (
    Account,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionSide,
)

DEFAULT_API_URL = "https://mkapi2.dfcfs.com/finskillshub"
COMMISSION_RATE = 0.0003  # 万三，与模拟撮合一致

# 委托状态：4=已成，8=已撤
_STATUS_FILLED = 4
_STATUS_CANCELLED = 8


def _sec_code(symbol: str) -> str:
    """600519.SH -> 600519（妙想 secCode 不带后缀）"""
    return str(symbol).split(".")[0]


def _symbol(sec_code: str) -> str:
    """600519 -> 600519.SH（按代码前缀补交易所后缀）"""
    code = str(sec_code)
    if code.startswith(("60", "68", "5", "11")):
        return f"{code}.SH"
    if code.startswith(("00", "30", "12", "15")):
        return f"{code}.SZ"
    if code.startswith(("8", "4", "92")):
        return f"{code}.BJ"
    return code


class MxBroker(BrokerAdapter):
    mode = MODE_LIVE
    broker_code = "mx"

    def __init__(
        self,
        *,
        apikey: str | None = None,
        api_url: str | None = None,
        dry_run: bool | None = None,
        timeout: float = 15.0,
        verify_filled: bool = True,
    ) -> None:
        self.apikey = apikey or getattr(settings, "MX_APIKEY", "")
        self.api_url = (
            api_url or getattr(settings, "MX_API_URL", "") or DEFAULT_API_URL
        ).rstrip("/")
        self.dry_run = (
            bool(getattr(settings, "BROKER_DRY_RUN", True)) if dry_run is None else dry_run
        )
        self.timeout = timeout
        self.verify = verify_filled

    # ---------------- 基础 ----------------
    def is_ready(self) -> tuple[bool, str]:
        if not self.apikey:
            return False, "未配置妙想凭证（MX_APIKEY），实盘不可用"
        if self.dry_run:
            return True, "实盘（DRY-RUN：不会发起真实下单）"
        return True, "实盘就绪"

    def _post(self, path: str, body: dict) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.api_url}{path}",
                    headers={"apikey": self.apikey, "Content-Type": "application/json"},
                    json=body,
                )
        except httpx.HTTPError as e:
            raise RuntimeError(f"妙想接口请求失败: {e}") from e
        return resp.json()

    # ---------------- 查询 ----------------
    def get_account(self) -> Account:
        ready, msg = self.is_ready()
        if not ready:
            return Account(account_id="", total_assets=0.0, cash_balance=0.0)
        if self.dry_run:
            return Account(
                account_id=f"MX_DRY_{self.apikey[:6] or 'NONE'}",
                total_assets=0.0,
                cash_balance=0.0,
            )
        res = self._post("/api/claw/mockTrading/balance", {"moneyUnit": 1})
        data = res.get("data") or {}
        total = float(data.get("totalAssets") or 0)
        avail = float(data.get("availBalance") or 0)
        pos_value = total - avail
        return Account(
            account_id=f"MX_{self.apikey[:6]}",
            total_assets=total,
            cash_balance=avail,
            frozen_cash=0.0,
            positions=self.get_positions(),
        )

    def get_positions(self) -> list[Position]:
        ready, _ = self.is_ready()
        if not ready or self.dry_run:
            return []
        res = self._post("/api/claw/mockTrading/positions", {"moneyUnit": 1})
        out: list[Position] = []
        for p in (res.get("data") or {}).get("posList") or []:
            price = self._dec(p.get("price"), p.get("priceDec"))
            cost = self._dec(p.get("costPrice"), p.get("costPriceDec"))
            qty = int(p.get("count") or 0)
            if qty <= 0:
                continue
            out.append(
                Position(
                    symbol=_symbol(p.get("secCode", "")),
                    side=PositionSide.LONG,
                    quantity=qty,
                    avg_price=cost,
                    market_value=price * qty,
                    unrealized_pnl=(price - cost) * qty,
                )
            )
        return out

    @staticmethod
    def _dec(raw: Any, dec: Any) -> float:
        """妙想价格按 10^priceDec 放大传输，需还原。"""
        try:
            return float(raw) / (10 ** int(dec or 0)) if raw is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    def get_orders(self, status: int = 0) -> list[dict]:
        res = self._post(
            "/api/claw/mockTrading/orders",
            {"fltOrderDrt": 0, "fltOrderStatus": status},
        )
        return (res.get("data") or {}).get("orders") or []

    def get_order_status(self, order_id: str) -> Order | None:
        ready, _ = self.is_ready()
        if not ready or self.dry_run:
            return None
        for o in self.get_orders(0):
            if str(o.get("id") or o.get("orderId")) == str(order_id):
                return self._to_order(o)
        return None

    def _to_order(self, o: dict) -> Order:
        qty = int(o.get("count") or o.get("quantity") or 0)
        traded = int(o.get("tradeCount") or 0)
        price = self._dec(o.get("price"), o.get("priceDec")) or self._dec(
            o.get("tradePrice"), o.get("priceDec")
        )
        status = OrderStatus.FILLED if int(o.get("status") or 0) == _STATUS_FILLED else OrderStatus.PENDING
        return Order(
            order_id=str(o.get("id") or o.get("orderId") or ""),
            symbol=_symbol(o.get("secCode", "")),
            order_type=OrderType.LIMIT,
            side=OrderSide.BUY if int(o.get("drt") or 1) == 1 else OrderSide.SELL,
            quantity=qty,
            price=price,
            filled_quantity=traded,
            status=status,
        )

    def get_market_price(self, symbol: str) -> float:
        ready, _ = self.is_ready()
        if not ready or self.dry_run:
            return 0.0
        for p in self.get_positions():
            if p.symbol == symbol:
                return p.market_value / p.quantity if p.quantity else 0.0
        return 0.0

    # ---------------- 下单 / 撤单 ----------------
    def place_order(self, order: Order) -> Order:
        ready, msg = self.is_ready()
        if not ready:
            order.status = OrderStatus.REJECTED
            order.message = msg
            return order

        if self.dry_run:
            # 不发起真实请求，仅返回模拟成交结果以打通链路
            order.order_id = f"DRY{uuid4().hex[:12].upper()}"
            order.status = OrderStatus.FILLED
            order.filled_quantity = order.quantity
            order.filled_at = datetime.now()
            order.price = order.price or 0.0
            order.commission = round(order.price * order.quantity * COMMISSION_RATE, 2)
            order.message = "DRY-RUN：未发起真实下单"
            return order

        body = {
            "type": "buy" if order.side == OrderSide.BUY else "sell",
            "stockCode": _sec_code(order.symbol),
            "quantity": order.quantity,
        }
        if order.order_type == OrderType.MARKET or not order.price:
            body["useMarketPrice"] = True
            body["price"] = 0
        else:
            body["useMarketPrice"] = False
            body["price"] = round(order.price, 2)

        res = self._post("/api/claw/mockTrading/trade", body)
        top_code = str(res.get("code", ""))
        data = res.get("data") or {}
        if top_code and top_code != "200":
            order.status = OrderStatus.REJECTED
            order.message = f"下单失败 code={top_code} msg={res.get('message')}"
            return order
        rc = data.get("rc")
        if rc is None:
            rc = (data.get("result") or {}).get("rc")
        if rc != 0:
            rmsg = (
                data.get("rmsg")
                or (data.get("result") or {}).get("rmsg")
                or res.get("message")
                or "unknown"
            )
            order.status = OrderStatus.REJECTED
            order.message = f"下单失败 rc={rc} msg={rmsg}"
            return order

        order.order_id = (
            data.get("orderID") or data.get("orderId") or f"MX{uuid4().hex[:12].upper()}"
        )
        order.broker_order_id = order.order_id

        if not self.verify:
            order.status = OrderStatus.PENDING
            order.message = "已报单（未做成交校验）"
            return order

        # 成交双校验：下单成功 ≠ 成交，轮询委托直到 status=4 或 8
        fill = self.verify_filled(
            order.symbol, order.quantity, drt=1 if order.side == OrderSide.BUY else 2
        )
        if fill.get("filled"):
            order.status = OrderStatus.FILLED
            order.filled_quantity = order.quantity
            order.filled_at = datetime.now()
            if fill.get("avgPrice"):
                order.price = float(fill["avgPrice"])
            order.commission = round((order.price or 0) * order.quantity * COMMISSION_RATE, 2)
        else:
            order.status = OrderStatus.PENDING
            order.message = f"已报单未确认成交：{fill.get('reason') or 'unknown'}"
        return order

    def cancel_order(self, order_id: str) -> bool:
        ready, _ = self.is_ready()
        if not ready or self.dry_run:
            return False
        target = None
        for o in self.get_orders(0):
            if str(o.get("id") or o.get("orderId")) == str(order_id):
                target = o
                break
        if not target:
            return False
        res = self._post(
            "/api/claw/mockTrading/cancel",
            {
                "type": "order",
                "orderId": str(order_id),
                "stockCode": target.get("secCode", ""),
            },
        )
        return str(res.get("code", "")) == "200"

    def verify_filled(
        self, symbol: str, quantity: int, drt: int = 1, max_wait: int = 20
    ) -> dict:
        """轮询确认成交。drt=1 买 / 2 卖。返回 {filled, status, tradeCount, avgPrice, reason?}"""
        sec = _sec_code(symbol)
        end = time.time() + max_wait
        last: dict = {}
        while time.time() < end:
            try:
                cands = [
                    o
                    for o in self.get_orders(0)
                    if str(o.get("secCode")) == sec and int(o.get("drt") or 0) == drt
                ]
                if cands:
                    cands.sort(key=lambda x: x.get("time", 0), reverse=True)
                    o = cands[0]
                    status = int(o.get("status") or 0)
                    traded = int(o.get("tradeCount") or 0)
                    last = {
                        "status": status,
                        "tradeCount": traded,
                        "avgPrice": self._dec(o.get("tradePrice"), o.get("priceDec")),
                        "orderId": o.get("id") or o.get("orderId"),
                    }
                    if status == _STATUS_FILLED and traded >= quantity:
                        last["filled"] = True
                        return last
                    if status == _STATUS_CANCELLED:
                        last["filled"] = False
                        last["reason"] = "cancelled"
                        return last
            except Exception as e:  # noqa: BLE001
                last = {"error": str(e)}
            time.sleep(2)
        last["filled"] = False
        last["reason"] = "timeout"
        return last
