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
    FUNDAMENTAL_PROVIDER: str = "tushare"  # tushare / akshare

    # AlphaFeed 行情数据源（A股/美股/港股 K线，REST: X-API-Key 认证）
    ALPHAFEED_KEY: str | None = None
    ALPHAFEED_BASE_URL: str = "https://api.alphafeed.org"

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
