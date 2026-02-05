from logging.config import fileConfig
from sqlalchemy import create_engine, engine_from_config
from sqlalchemy import pool
from alembic import context
import os
import sys

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 动态导入以避免循环依赖
def import_models():
    from app.core.database import Base
    from app.models import user, quant
    return Base.metadata

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = import_models()

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # 从环境变量获取数据库URL
    try:
        from app.core.config import settings
        database_url = settings.DATABASE_URL
    except ImportError:
        # 如果无法导入settings，使用alembic.ini中的配置
        database_url = config.get_main_option("sqlalchemy.url")

    connectable = create_engine(database_url)

    with connectable.connect() as connection:
        context.configure(
            connection=connection, 
            target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()