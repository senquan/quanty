"""事件关键词表（确定性筛子，P1-2）

形态参照 Vibe-Research ``datasources/chokepoint_keywords.json``：
  - keywords: 命中任一即打该事件类别；``re:`` 前缀为正则（中文需上下文约束时用）
  - negatives: 同一子句内命中则否决该类别（并列事项不互相否决——先按标点拆子句）
  - decision_hint: 该事件对研究意味着什么（给 P2/P3 画像与因子加权做参考）

判定规则（与 Vibe 一致）：
  ① 标题/摘要先按 ;；,，。！？\n 拆子句；
  ② keywords / negatives 在**同一子句**内判定；
  ③ 英文关键词按词边界；中文直接包含匹配；
  ④ 预筛只做"是否值得送 LLM"的判断，事件类别本身是附产物（P2 画像可用）。

只收与"对单标的研究有用"相关的事件类；纯娱乐/体育类文本天然全不命中，
这正是预筛过滤 70%+ 无用文本的机制之一。
"""
from __future__ import annotations

import re

# 子句切分：并列事项互不否决
_CLAUSE_RE = re.compile(r"[;；,，。！？!?\n\r]+")

# 英文按词边界判定时使用
_ASCII_WORD_RE = re.compile(r"[a-zA-Z0-9]+")

EVENT_CATEGORIES: dict[str, dict] = {
    "业绩": {
        "keywords": ["财报", "季报", "年报", "中报", "业绩预告", "业绩快报",
                     "净利润", "营收", "盈利", "亏损", "预盈", "预亏", "扭亏",
                     "毛利率", "EPS", "净利"],
        "negatives": ["停牌"],
        "decision_hint": "基本面事件：P2 计算作者历史准确度的最佳锚点（提及后 20/60 日可验证）",
    },
    "涨价": {
        "keywords": ["涨价", "提价", "调价函", "价格上调", "上调价格", "price increase"],
        "negatives": ["不涨价", "未涨价", "不提价", "降价"],
        "decision_hint": "供给紧信号；看涨幅是否传导到毛利率",
    },
    "扩产": {
        "keywords": ["扩产", "扩建", "新建产能", "产能扩张", "达产", "产能释放",
                     "re:(项目|产线|工厂|基地).{0,8}投产", "re:签约.{0,6}(框架|合作|战略)"],
        "negatives": ["停产", "减产"],
        "decision_hint": "成长叙事证据；关注资本开支与行业供需",
    },
    "并购重组": {
        "keywords": ["并购", "收购", "重组", "借壳", "要约收购", "资产注入",
                     "merger", "acquisition"],
        "negatives": ["终止", "终止收购", "放弃收购"],
        "decision_hint": "事件驱动；注意否决词优先级",
    },
    "订单合同": {
        "keywords": ["中标", "re:签订.{0,8}(合同|协议|订单)", "重大合同",
                     "框架协议", "订单", "contract", "wins bid"],
        "negatives": ["取消", "终止"],
        "decision_hint": "景气度直接证据；关注金额占比",
    },
    "回购增持": {
        "keywords": ["回购", "增持", "re:(回购|增持).{0,6}(股份|股票|计划)"],
        "negatives": ["减持", "解除", "终止回购"],
        "decision_hint": "管理层/大股东信心信号",
    },
    "减持质押": {
        "keywords": ["减持", "质押", "平仓", "解禁", "pledge"],
        "negatives": ["增持", "回购"],
        "decision_hint": "资金面压力信号",
    },
    "政策监管": {
        "keywords": ["政策", "监管", "处罚", "立案", "调查", "征求意见稿",
                     "发改委", "证监会", "工信部", "regulation"],
        "negatives": [],
        "decision_hint": "行业贝塔/风险事件",
    },
    "技术突破": {
        "keywords": ["突破", "量产", "点亮", "首次", "国产替代", "技术验证",
                     "breakthrough", "milestone"],
        "negatives": ["失败", "延期"],
        "decision_hint": "成长叙事；注意'首次'歧义，需 LLM 复核上下文",
    },
    "机构观点": {
        "keywords": ["研报", "评级", "目标价", "首予", "维持买入", "上调评级",
                     "下调评级", "rating", "price target"],
        "negatives": [],
        "decision_hint": "卖方共识信号；P2 可算作者与卖方观点的共振/背离",
    },
}

_CLAUSE_SPLIT = _CLAUSE_RE.split


def match_events(text: str) -> list[str]:
    """子句级事件判定：返回命中的事件类别列表（去重保序）。

    keywords/negatives 必须落在同一子句才算否决/成立；``re:`` 前缀按正则，
    纯 ASCII 关键词按词边界，其余做包含匹配。
    """
    if not text:
        return []
    hits: list[str] = []
    for cat, spec in EVENT_CATEGORIES.items():
        for clause in _CLAUSE_SPLIT(text):
            if _clause_hit(clause, spec["keywords"]) and not _clause_hit(clause, spec["negatives"]):
                hits.append(cat)
                break
    return hits


def _clause_hit(clause: str, kws: list[str]) -> bool:
    for kw in kws:
        if kw.startswith("re:"):
            try:
                if re.search(kw[3:], clause):
                    return True
            except re.error:  # noqa: PERF203 — 配置错误不应炸整轮
                continue
        elif kw.isascii() and any(ch.isalnum() for ch in kw):
            # 英文按词边界
            if re.search(rf"\b{re.escape(kw)}\b", clause, re.IGNORECASE):
                return True
        elif kw in clause:
            return True
    return False
