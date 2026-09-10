"""红线关键词：命中只标记（documents.redline JSONB），**不删除**——保留审计可见性。

种子参照 Vibe-Research datasources/rss_sources.json 的 redline_keywords
（博彩/加密/色情等合规红线）。P0 从简，后续可按源维护。
"""

# 博彩 / 色情 / 虚拟货币（涉 A 股合规噪音）/ 其他违法红线
REDLINE_KEYWORDS: list[str] = [
    # 博彩
    "博彩", "赌场", "赌球", "时时彩", "六合彩", "老虎机",
    # 色情
    "色情", "约炮", "裸聊",
    # 虚拟货币（对 A 股选股属合规噪音）
    "比特币", "币圈", "虚拟货币", "ICO", "合约带单",
    # 其他违法
    "代孕", "枪支", "走私",
]


def match_redline(*texts: str) -> list[str]:
    """返回命中的红线词列表（空列表 = 未命中）"""
    blob = " ".join(t for t in texts if t)
    return [kw for kw in REDLINE_KEYWORDS if kw in blob]
