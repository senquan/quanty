"""确定性预筛（L2a，零 LLM）：判断一篇文档是否值得送 LLM 抽取

判定 = 标的识别命中（代码正则 / 别名表）或 事件关键词命中。
设计依据 2026-09-07.intel-module-design.md §5：LLM 每篇都是真金白银，
预筛**零成本、可复现、可单测**，预期滤掉 70%+ 的无用文本。

标的识别三级：
  1. 6 位代码正则（600519 / 000858.SZ 等形态）→ 标准代码（带交易所后缀的直接归一）
  2. 别名表精确匹配（intel.symbol_alias：代码/简称/俗称），命中即回查标准代码
  3. 都没命中 → 该文档无标的锚点，除非事件词命中（行业级事件），否则不送 LLM

别名表全部载入内存（1.1 万条，MB 级），按 alias 长度降序匹配，
避免"贵州茅台"被"茅台"抢先截断的次序问题。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.intel.understand import keywords as kw

# 6 位代码（可带 .SH/.SZ/.BJ 后缀）：独立成词（前后不是数字）才算
_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?:\.(SH|SZ|BJ))?(?!\d)")
_SUFFIX = {"SH": ".SH", "SZ": ".SZ", "BJ": ".BJ", "": None}


@dataclass
class PrescreenResult:
    symbols: list[str] = field(default_factory=list)   # 标准代码（600519.SH）
    matched_by: dict[str, str] = field(default_factory=dict)  # symbol -> 命中别名/方式
    events: list[str] = field(default_factory=list)    # 事件类别
    hit: bool = False                                  # 是否送 LLM

    @property
    def reason(self) -> str:
        if self.symbols and self.events:
            return "symbol+event"
        if self.symbols:
            return "symbol"
        if self.events:
            return "event"
        return "miss"


class AliasIndex:
    """别名表内存索引：alias -> 标准代码（可一对多，取第一个 + 全集）"""

    def __init__(self, pairs: list[tuple[str, str]]):
        by_alias: dict[str, list[str]] = {}
        for alias, sym in pairs:
            by_alias.setdefault(alias, []).append(sym)
        # 长名称优先，防止"贵州茅台"被"茅台"级别的短别名截断
        self._sorted = sorted(by_alias.items(), key=lambda kv: -len(kv[0]))
        self.by_alias = by_alias

    @classmethod
    def from_db(cls) -> "AliasIndex":
        from app.intel.store import get_engine
        from sqlalchemy import text
        with get_engine().connect() as c:
            rows = c.execute(text(
                "SELECT alias, symbol FROM intel.symbol_alias"
            )).all()
        return cls([(r[0], r[1]) for r in rows])

    def find_in(self, text: str) -> list[str]:
        """返回 text 中命中的别名列表（用于回查 symbol；调用方决定上限）"""
        return [alias for alias, _ in self._sorted if alias in text]


# 模块级单例：进程内复用（1.1 万条 ~MB 级）
_alias_index: AliasIndex | None = None


def get_alias_index() -> AliasIndex:
    global _alias_index
    if _alias_index is None:
        _alias_index = AliasIndex.from_db()
    return _alias_index


def _normalize_code(code: str, suffix: str | None) -> str | None:
    """6 位裸代码按交易所段位补后缀：6xx→SH、0xx/3xx→SZ、4xx/8xx/9xx→BJ"""
    if suffix:
        return code + _SUFFIX[suffix]
    if code.startswith("6"):
        return code + ".SH"
    if code.startswith(("0", "3")):
        return code + ".SZ"
    if code.startswith(("4", "8", "9")):
        return code + ".BJ"
    return None


def find_symbols(text: str, index: AliasIndex | None = None) -> tuple[list[str], dict[str, str]]:
    """标的识别：返回 ([标准代码], {代码: 命中方式})"""
    index = index or get_alias_index()
    symbols: dict[str, str] = {}

    # 1) 6 位代码正则
    for m in _CODE_RE.finditer(text):
        code, suffix = m.group(1), m.group(2)
        sym = _normalize_code(code, suffix)
        if sym and sym not in symbols:
            symbols[sym] = m.group(0)

    # 2) 别名表（长名优先；命中官方简称/俗称/裸代码均归一到标准代码）
    for alias in index.find_in(text):
        for sym in index.by_alias[alias]:
            if sym not in symbols:
                symbols[sym] = alias
    return list(symbols), symbols


def prescreen(title: str, content: str | None = None,
              index: AliasIndex | None = None) -> PrescreenResult:
    """预筛一篇文档：标题+摘要（或正文前段）判定是否值得送 LLM"""
    text = f"{title}\n{(content or '')[:1500]}"
    syms, by = find_symbols(text, index)
    events = kw.match_events(text)
    res = PrescreenResult(symbols=syms, matched_by=by, events=events)
    res.hit = bool(syms or events)
    return res
