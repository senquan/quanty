"""服务配置模块

所有配置通过环境变量 / .env 注入，严禁硬编码密钥。
"""
from functools import lru_cache
from pathlib import Path

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

    # Application
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8100
    TZ: str = "Asia/Shanghai"

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
