"""AI 因子生成（Phase 3 步骤10）

将自然语言描述转换为受限 formula 表达式（接入 §10 沙箱），再注册为自定义因子。

实现策略（务实、安全、可离线）：
- 未配置 LLM 时，使用内置「规则引擎」：从描述中抽取关键词映射到因子模板，
  组合为白名单 formula（如 "动量" -> momentum, "20日" -> delay(close,20)）。
- 若配置 LLM_API_KEY，可调用外部 LLM 生成 formula（仍须经沙箱校验才接受，
  绝不执行 LLM 返回的原始代码）。

所有生成结果都受 app.factors.formula 的 AST 白名单约束，杜绝代码注入。
"""
import re

from app.core.logging import get_logger

logger = get_logger(__name__)


# 关键词 -> 指标模板（占位符 {n}=窗口）
_KEYWORD_TEMPLATES = [
    ("动量", "momentum", "adj_close / delay(adj_close, {n}) - 1"),
    ("动", "momentum", "adj_close / delay(adj_close, {n}) - 1"),
    ("收益率", "momentum", "adj_close / delay(adj_close, {n}) - 1"),
    ("反转", "reversal", "-1 * (adj_close / delay(adj_close, {n}) - 1)"),
    ("波动率", "volatility", "ts_std(adj_close, {n})"),
    ("波动", "volatility", "ts_std(adj_close, {n})"),
    ("标准差", "volatility", "ts_std(adj_close, {n})"),
    ("rsi", "rsi", "rank(rsi_proxy({n}))"),
    ("相对强度", "rel_strength", "(adj_close / delay(adj_close, {n}) - 1)"),
    ("乖离", "bias", "adj_close / ma(adj_close, {n}) - 1"),
    ("均线", "ma", "ma(adj_close, {n})"),
    ("成交额", "amount_rank", "rank(volume * adj_close)"),
    ("量比", "vol_ratio", "ma(volume, {n}) / ma(volume, 20)"),
    ("换手", "turnover", "volume / ma(volume, {n})"),
]


def _extract_window(text: str, default: int = 20) -> int:
    m = re.search(r"(\d+)\s*(日|天|周期|period)", text.lower())
    if m:
        return int(m.group(1))
    return default


def _rsi_proxy(n: int):
    """formula 不支持 rsi，给出 rolling 收益排名代理（在沙箱内用 rank(delay) 近似）"""
    return f"rank(adj_close / delay(adj_close, {n}) - 1)"


def generate_formula(description: str, llm_api_key: str | None = None) -> dict:
    """由自然语言生成因子定义

    返回: {"code","name","category","formula","frequency","source":"rule|llm"}
    """
    text = (description or "").strip()
    if not text:
        raise ValueError("描述不能为空")

    window = _extract_window(text)

    # 预留 LLM 接入点：配置了 key 时调用外部 LLM 生成 formula
    formula = None
    source = "rule"
    if llm_api_key:
        formula = _call_llm(description, llm_api_key)
        source = "llm"

    if not formula:
        # 规则引擎：取第一个命中的关键词模板
        for kw, category, tpl in _KEYWORD_TEMPLATES:
            if kw.lower() in text.lower():
                formula = tpl.format(n=window)
                if "rsi_proxy" in formula:
                    formula = _rsi_proxy(window)
                return {
                    "code": f"AI_{category.upper()}_{window}",
                    "name": f"AI生成-{kw}因子({window})",
                    "category": "momentum" if category in ("momentum", "reversal", "rel_strength") else category,
                    "formula": formula,
                    "frequency": "Daily",
                    "source": source,
                }
        # 未命中：默认动量因子
        formula = f"adj_close / delay(adj_close, {window}) - 1"
        return {
            "code": f"AI_MOMENTUM_{window}",
            "name": f"AI生成-动量因子({window})",
            "category": "momentum",
            "formula": formula,
            "frequency": "Daily",
            "source": source,
        }

    return {
        "code": f"AI_CUSTOM_{abs(hash(description)) % 100000:05d}",
        "name": f"AI生成-{description[:20]}",
        "category": "momentum",
        "formula": formula,
        "frequency": "Daily",
        "source": source,
    }


def _call_llm(description: str, api_key: str) -> str | None:
    """调用外部 LLM 生成 formula（占位实现）

    真实接入时需注入 LLM 返回文本并解析出 formula 字段；此处返回 None，
    走规则引擎兜底。接入示例：
        import httpx
        resp = httpx.post(..., headers={"Authorization": f"Bearer {api_key}"})
        return _parse_formula(resp.json())
    """
    logger.info("LLM 接入点未启用，使用规则引擎生成", extra={"has_key": bool(api_key)})
    return None
