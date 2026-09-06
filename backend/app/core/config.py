from pydantic_settings import BaseSettings
from typing import List
import json

class Settings(BaseSettings):
    # Database
    DATABASE_URL: str
    
    # JWT
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # Application
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    # CORS
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # 策略内部下单令牌（data-cleaner 调仓任务携带 X-Internal-Token 调用 /trading/orders/internal）
    STRATEGY_INTERNAL_TOKEN: str = ""
    # WebSocket 集成令牌（backend ↔ data-cleaner 长连接握手校验，与 dc 的 WS_TOKEN 一致）
    STRAT_INTEGRATION_TOKEN: str = ""

    # 交易：默认模式（paper / live）与模拟盘初始资金
    BROKER_MODE: str = "paper"
    TRADING_INITIAL_CAPITAL: float = 1000000

    # 实盘（东方财富妙想）：BROKER_DRY_RUN=True 时不会发起任何真实请求
    BROKER_DRY_RUN: bool = True
    MX_APIKEY: str = ""
    MX_API_URL: str = "https://mkapi2.dfcfs.com/finskillshub"

    # 策略调仓编排（原由 data-cleaner 驱动，职责归位后由 backend 承担）
    # 多副本部署时只在唯一实例上开启；调仓记录另有唯一约束兜底幂等
    ENABLE_TRADING_SCHEDULER: bool = False
    # 因子底册同步（刷新已入库因子的口径与效能指标）
    # 同交易调度：多副本部署时只能有一个实例开启
    ENABLE_FACTOR_SYNC: bool = False
    FACTOR_SYNC_INTERVAL_MIN: int = 60
    # 清洗服务存活轮询：默认开启，每 30s 刷新 cleaner_services 状态，
    # 驱动因子可用性（available）随 dc 上下线自动刷新。多副本部署时仅一个实例开启。
    ENABLE_CLEANER_POLL: bool = True
    CLEANER_POLL_INTERVAL_SEC: int = 30
    # 组合盘后估值：交易日 15:30 从 dc 拉行情、更新持仓市值、记录日快照。
    # 多副本部署时仅一个实例开启；快照表另有唯一约束兜底幂等。
    ENABLE_PORTFOLIO_VALUATION: bool = True
    REBALANCE_MODE: str = "paper"
    # 资金分配：可用资金使用率与整手股数（默认沿用原 data-cleaner 逻辑）
    REBALANCE_CASH_USAGE: float = 0.95
    REBALANCE_LOT_SIZE: int = 100

    # 风控：单笔订单金额上限（占总资产比例）。原为绝对值 10 万，账户规模变化或
    # 标的数较少时会误拒（100 万账户买 5 只标的每笔 19 万即全被拒）。
    RISK_MAX_ORDER_PCT: float = 0.3

    # ---- WebSocket 长连接（backend 作为服务端，接收 N 个 dc 实例连入）----
    # 设计见 docs/plans/2026-09-04.ws-dc-backend.md
    # 总开关：关闭时不挂载 /ws/dc，行为与改造前完全一致（纯 HTTP 轮询）
    WS_ENABLED: bool = False
    WS_MAX_CONNECTIONS: int = 50
    WS_MAX_FRAME_BYTES: int = 256 * 1024
    WS_PING_INTERVAL_SEC: float = 15.0
    WS_PING_TIMEOUT_SEC: float = 20.0
    # 事件去重：无 Redis 时的内存 LRU 容量（Redis 可用时额外做跨重启去重）
    WS_DEDUP_CACHE_SIZE: int = 10000
    # 连接被认为已失联的秒数（超过则按离线处理）
    WS_CONNECTION_STALE_SEC: int = 120
    # 清洗服务存活轮询：已建立 WS 连接的实例跳过轮询，仅对未连接实例兜底
    CLEANER_POLL_SKIP_CONNECTED: bool = True
    # 因子副本每日对账（发现缺失 / 冗余 / 陈旧），需配合 ENABLE_FACTOR_SYNC
    ENABLE_FACTOR_RECONCILE: bool = False
    FACTOR_RECONCILE_HOUR: int = 7
    FACTOR_RECONCILE_MINUTE: int = 30

    @property
    def allowed_origins_list(self) -> List[str]:
        """Convert comma-separated origins string to list"""
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(',') if origin.strip()]
    
    class Config:
        env_file = ".env"

settings = Settings()