"""服务配置模块

所有配置通过环境变量 / .env 注入，严禁硬编码密钥。
"""
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """data-cleaner 服务配置"""

    # Database (复用主后端 PG, 使用独立 schema: factor)
    DATABASE_URL: str = "postgresql+asyncpg://quant_user:quant_password@localhost:5432/quant_db"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # 因子 parquet 数据目录
    FACTOR_DATA_DIR: str = "./data/factors"

    # 失败输入快照目录（便于排查）
    QUARANTINE_DIR: str = "./data/quarantine"

    # 财务数据源（价值/成长因子）
    TUSHARE_TOKEN: str | None = None
    FUNDAMENTAL_PROVIDER: str = "auto"  # tushare / akshare / auto(优先tushare,缺权限兜底akshare)

    # AlphaFeed 行情数据源（A股/美股/港股 K线，REST: X-API-Key 认证）
    ALPHAFEED_KEY: str | None = None
    ALPHAFEED_BASE_URL: str = "https://api.alphafeed.org"

    # Pandadata 数据源（A股日线/前复权/财报等；SDK panda_data==0.0.12，需 init_token 鉴权）
    # 凭据由 Pandadata 服务方提供，base_url 对应其 JAVA_SERVICE_BASE_URL。
    # 未配置时 pandadata 路径不可用（init_token 失败），不影响其他数据源。
    PANDADATA_USERNAME: str | None = None
    PANDADATA_PASSWORD: str | None = None
    PANDADATA_BASE_URL: str | None = None

    # ---- WebSocket 长连接（dc 作为客户端主动连入 backend，1 对多）----
    # 设计见 docs/plans/2026-09-04.ws-dc-backend.md
    # 总开关：关闭时 dc 行为与改造前完全一致（纯 HTTP），便于一键回退
    WS_ENABLED: bool = False
    # backend 的 WS 地址（ws:// / wss://）；留空则由 BACKEND_BASE_URL 自动推导
    BACKEND_WS_URL: str = ""
    # 本实例身份：backend 连接注册表的键，多实例部署时必须唯一
    WS_INSTANCE_ID: str = ""
    # 对应 backend cleaner_services.service_code（副本归属）
    WS_SERVICE_CODE: str = ""
    # 心跳：WS 原生 ping 间隔 / 超时（秒）。
    # 注意：LB / 代理的空闲超时必须大于 ping 间隔，否则连接会被静默踢掉
    WS_PING_INTERVAL_SEC: float = 15.0
    WS_PING_TIMEOUT_SEC: float = 20.0
    WS_OPEN_TIMEOUT_SEC: float = 10.0
    # 重连：指数退避 + 抖动（按 instance_id 哈希错峰，防多实例惊群）
    WS_RECONNECT_MIN_SEC: float = 0.5
    WS_RECONNECT_MAX_SEC: float = 30.0
    WS_RECONNECT_MAX_TIMES: int = 0  # 0 表示不限制次数（由熔断逻辑控制）
    # outbox：发送侧缓冲
    WS_OUTBOX_TTL_HOURS: int = 24
    WS_OUTBOX_MAX_RECORDS: int = 10000
    # 单帧上限（字节），与 protocol.MAX_FRAME_BYTES 对齐
    WS_MAX_FRAME_BYTES: int = 256 * 1024
    # 未 ack 消息数上限（背压保护）
    WS_MAX_INFLIGHT: int = 1000

    # Application
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8100
    TZ: str = "Asia/Shanghai"

    # 主后端地址与策略内部下单令牌（模拟盘调仓时调用主后端内部端点）
    BACKEND_BASE_URL: str = "http://localhost:8000"
    STRATEGY_INTERNAL_TOKEN: str = ""

    # ---- 网关接入认证（阶段 A：供主后端 registry 管理）----
    # 多个 key 用逗号分隔；主后端在 registry 中保存对应 key，用于 QoS 轮询 / 因子拉取。
    # 留空表示关闭认证（开发期友好，生产务必配置）。
    # 注：声明为 str（pydantic-settings 的 EnvSettingsSource 对 list[str] 会按 JSON 解析，
    # 逗号分隔无法被 env 源正确读取），解析后的列表通过 property `api_keys` 暴露。
    SERVICE_NAME: str = "cleaner-dev"
    API_KEYS: str = ""                          # 例: "k_prod_xxx,k_staging_yyy"

    @property
    def api_keys(self) -> list[str]:
        """解析后的 API key 列表"""
        return [k.strip() for k in self.API_KEYS.split(",") if k.strip()] if self.API_KEYS else []

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def factor_data_path(self) -> Path:
        """因子数据目录（自动创建）"""
        path = Path(self.FACTOR_DATA_DIR)
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    """配置单例（进程内缓存）"""
    return Settings()


settings = get_settings()
