"""因子注册表"""
import pandas as pd

from app.factors.base import Factor

_REGISTRY: dict[str, Factor] = {}


def register(cls: type[Factor]) -> type[Factor]:
    """装饰器：注册因子实现"""
    _REGISTRY[cls.code] = cls()
    return cls


def get_factor(code: str) -> Factor:
    from app.core.exceptions import FactorNotFoundError

    if code not in _REGISTRY:
        raise FactorNotFoundError(f"因子未注册: {code}")
    return _REGISTRY[code]


def list_factors(category: str | None = None) -> list[dict]:
    """返回所有因子元数据（可按类别过滤）"""
    items = [f.get_metadata() for f in _REGISTRY.values()]
    if category:
        items = [i for i in items if i["category"] == category]
    return items


def compute_factor(code: str, df: "pd.DataFrame") -> "pd.Series":
    """便捷函数：直接按 code 计算因子"""
    return get_factor(code).compute(df)


# 注册内置因子
from app.factors.fundamental import *  # noqa: F401,F403,E402
from app.factors.intraday import *  # noqa: F401,F403,E402
from app.factors.liquidity import *  # noqa: F401,F403,E402
from app.factors.momentum import *  # noqa: F401,F403,E402
from app.factors.sentiment import *  # noqa: F401,F403,E402
from app.factors.size import *  # noqa: F401,F403,E402
from app.factors.technical import *  # noqa: F401,F403,E402
from app.factors.volatility import *  # noqa: F401,F403,E402
