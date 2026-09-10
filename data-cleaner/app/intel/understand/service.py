"""理解层批跑编排（P1-6）

一轮 run_understanding_batch：
  取未抽取文档 → 确定性预筛（零 LLM）→ 命中者送 LLM 抽取 →
  pydantic strict + span 硬校验（失败落 quarantine）→ doc_mentions / doc_style 落库。

预算纪律：每篇调用前过 BudgetGate，超限 BudgetExceeded 直接中止整批（告警已由 gate 打）。
预筛未命中的文档记 reason='miss'，等 P2 若需要行业级信号再回捞——不做静默丢弃。
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from app.intel import store
from app.intel.core.config import settings
from app.intel.core.logging import get_logger
from app.intel.understand import prescreen as pre
from app.intel.understand import prompts
from app.intel.understand.llm.client import BudgetExceeded, LLMClient, LLMResponse
from app.intel.understand.schema import parse_extraction

logger = get_logger(__name__)


def _clip_text(title: str, content: str | None) -> str:
    """送审文本：标题 + 正文，超长截头（成本护栏）"""
    max_chars = int(getattr(settings, "INTEL_LLM_MAX_INPUT_CHARS", 8000))
    text = f"{title}\n\n{(content or '').strip()}"
    return text[:max_chars]


def run_understanding_batch(limit: int = 100, client: LLMClient | None = None,
                            index: pre.AliasIndex | None = None,
                            doc_ids: list[int] | None = None) -> dict:
    """批跑一轮。dry-run 不涉及（预筛报告在 scripts 侧独立做）。

    doc_ids: 只抽这批文档（上传后立即抽取用）。
    """
    client = client or LLMClient()
    index = index or pre.get_alias_index()
    docs = store.docs_for_understanding(limit, doc_ids=doc_ids)

    summary = {"candidates": len(docs), "prescreen_hit": 0, "prescreen_miss": 0,
               "extracted": 0, "quarantined": 0, "no_mention": 0,
               "api_fail": 0, "cost_cny": 0.0, "stopped_reason": None}

    # 先整批预筛（零 LLM、毫秒级），命中者并发送审
    tasks = []
    for doc in docs:
        pres = pre.prescreen(doc["title"], doc["content"], index)
        if not pres.hit:
            summary["prescreen_miss"] += 1
            continue
        summary["prescreen_hit"] += 1
        tasks.append((doc, pres))

    lock = Lock()
    budget_stopped = False

    def _one(doc: dict, pres) -> None:
        nonlocal budget_stopped
        text = _clip_text(doc["title"], doc["content"])
        user = prompts.EXTRACT_USER_TMPL.format(
            candidates="、".join(pres.symbols[:10]) or "（事件级，无具体标的）",
            text=text,
        )
        try:
            resp = client.extract_once(prompts.EXTRACT_SYSTEM_PROMPT, user, doc_id=doc["id"])
        except BudgetExceeded:
            with lock:
                budget_stopped = True
            return
        except Exception as e:  # noqa: BLE001 — 单篇 API 失败不拖垮整批
            with lock:
                summary["api_fail"] += 1
            logger.warning(f"intel 理解 doc={doc['id']} API 失败: {e}",
                           extra={"task": "intel_understand"})
            return

        mentions, style, err = parse_extraction(resp.content, text)
        if err is not None:
            with lock:
                summary["cost_cny"] += resp.cost_cny
                summary["quarantined"] += 1
            store.insert_quarantine(doc["id"], "schema", err,
                                    {"raw": resp.content[:2000]})
            logger.warning(f"intel 理解 doc={doc['id']} 落隔离区: {err}",
                           extra={"task": "intel_understand"})
            return
        # 同 doc 内按 symbol 去重（LLM 偶发重复抽取同一标的，如 doc 1 抽了 3 条 300308.SZ）
        seen = set()
        uniq = []
        for m in mentions:
            if m.symbol in seen:
                continue
            seen.add(m.symbol)
            uniq.append(m)
        with lock:
            summary["cost_cny"] += resp.cost_cny
            if uniq:
                summary["extracted"] += len(uniq)
            else:
                summary["no_mention"] += 1
        for m in uniq:
            store.insert_mention({
                "doc_id": doc["id"], "symbol": m.symbol,
                "stance": m.stance.value, "confidence": m.confidence,
                "horizon": m.horizon.value, "thesis": m.thesis,
                "evidence": m.evidence, "span_start": m.span_start,
                "span_end": m.span_end, "method": "llm",
                "prompt_version": prompts.PROMPT_VERSION, "llm_version": client.llm_version,
            })
        if style is not None and style.normalized():
            store.insert_style(doc["id"], style.normalized(),
                               prompts.PROMPT_VERSION, client.llm_version)

    concurrency = max(1, int(getattr(settings, "INTEL_LLM_CONCURRENCY", 4)))
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_one, doc, pres): doc["id"] for doc, pres in tasks}
        for fut in as_completed(futures):
            # 未捕获异常必须在这里暴露——吞掉会让整批静默全 0
            fut.result()
            if budget_stopped:
                summary["stopped_reason"] = "budget"
                break  # 已提交的任务会跑完（gate 逐调用拦截，不会超支）
    if budget_stopped and not summary["stopped_reason"]:
        summary["stopped_reason"] = "budget"

    return summary


def prescreen_report(docs: list[dict], index: pre.AliasIndex | None = None) -> dict:
    """对存量文档跑预筛统计（纯零 LLM，可反复跑）：过滤率 = miss 占比"""
    index = index or pre.get_alias_index()
    hit = miss = with_sym = with_event = 0
    symbol_counter: dict[str, int] = {}
    miss_samples: list[dict] = []
    for d in docs:
        pres = pre.prescreen(d["title"], d["content"], index)
        if pres.hit:
            hit += 1
            with_sym += bool(pres.symbols)
            with_event += bool(pres.events)
            for s in pres.symbols:
                symbol_counter[s] = symbol_counter.get(s, 0) + 1
        else:
            miss += 1
            if len(miss_samples) < 5:
                miss_samples.append({"id": d["id"], "title": d["title"][:50]})
    total = hit + miss
    return {
        "total": total, "hit": hit, "miss": miss,
        "filter_rate": round(miss / total, 4) if total else 0.0,
        "hit_by_symbol": with_sym, "hit_by_event_only": with_event,
        "top_symbols": sorted(symbol_counter.items(), key=lambda kv: -kv[1])[:15],
        "miss_samples": miss_samples,
    }
