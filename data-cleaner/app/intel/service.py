"""intel 摄取闭环编排（P0）

一轮 run_rss_ingest：
  查 enabled rss 源 → ThreadPoolExecutor 并发拉取（单源失败隔离在 RSSSource 内）→
  逐篇规范化 → 三级幂等去重（external_id / canonical_url / content_hash）→
  simhash 转载识别（duplicate_of_id 指向首发）→ 原文不可变落盘（raw_path）→
  INSERT documents（available_at = max(published, ingested) 防前视）→ feed_health。

同步实现，由 tasks 经 run_in_executor 调度（与 dc 盘后任务同款隔离）。
多源共振不去重丢弃：转载仍入库并标记 duplicate_of_id（共振本身是热度信号）。
"""
from __future__ import annotations

import json
import shutil
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.intel import store
from app.intel.core.config import settings
from app.intel.core.logging import get_logger
from app.intel.ingest.base import FetchResult
from app.intel.ingest.manual import ManualSource, _SUPPORTED_EXT
from app.intel.ingest.registry import create_source
from app.intel.normalize import dedupe, redline
from app.intel.normalize import text as norm_text

logger = get_logger(__name__)


def _raw_dir() -> Path:
    p = Path(settings.INTEL_RAW_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_raw(hash_hex: str, content_html: str) -> str:
    """原文不可变落盘：文件名=content_hash，存在即跳过（同 hash 同内容）。返回绝对路径

    必须按字节写（write_bytes）：write_text 在 Windows 文本模式会把 \n 翻译成 \r\n，
    导致 sha256(文件) != content_hash（联调实测 69/188 不匹配）。
    """
    path = _raw_dir() / f"{hash_hex}.html"
    if not path.exists():
        path.write_bytes(content_html.encode("utf-8"))
    return str(path)


def _fetch_all(sources: list[dict], timeout: float, limit: int, workers: int) -> dict[int, FetchResult]:
    """并发拉取所有源 → {source_id: FetchResult}（单源失败已隔离在 FetchResult 内）"""
    out: dict[int, FetchResult] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {
            ex.submit(
                create_source(s["source_type"], s["name"]).fetch,
                s["url"], limit=limit, timeout=timeout,
            ): s
            for s in sources
        }
        for f in as_completed(futs):
            src = futs[f]
            try:
                out[src["id"]] = f.result()
            except Exception as e:  # noqa: BLE001 — 双保险：源实现外再兜一层
                out[src["id"]] = FetchResult(
                    source_name=src["name"], ok=False,
                    error=f"{type(e).__name__}: {str(e)[:150]}",
                )
    return out


def _ingest_one_source(src: dict, result: FetchResult) -> dict:
    """单源：规范化 → 去重 → 转载识别 → 落盘 → 入库。返回该源统计"""
    stats = {
        "source": src["name"], "fetched": len(result.items),
        "new": 0, "dup": 0, "reposts": 0, "last_item_at": None,
        # 本次真正新插入的 doc id —— 上层（上传接口）可据此对"这一批"立即抽取，
        # 不用去猜 id 区间或按 source 反查（并发上传同一源时会串）。
        "doc_ids": [],
    }
    if not result.items:
        return stats

    # 逐篇预计算规范化字段
    prepared = []
    for it in result.items:
        chash = dedupe.content_hash(it.content_html or it.title)
        prepared.append({
            "item": it,
            "content_hash": chash,
            "canonical_url": norm_text.canonical_url(it.url),
            "text": norm_text.normalize_text(it.content_html),
        })

    existing = store.existing_document_keys(
        src["id"],
        [p["item"].external_id for p in prepared if p["item"].external_id],
        [p["canonical_url"] for p in prepared if p["canonical_url"]],
        [p["content_hash"] for p in prepared],
    )
    sim_pool = store.simhash_candidates(settings.INTEL_DEDUPE_WINDOW_DAYS)

    for p in prepared:
        it = p["item"]
        # 三级幂等去重：同源 guid → canonical_url → content_hash
        if it.external_id and it.external_id in existing["eids"]:
            stats["dup"] += 1
            continue
        if p["canonical_url"] and p["canonical_url"] in existing["urls"]:
            stats["dup"] += 1
            continue
        if p["content_hash"] in existing["hashes"]:
            stats["dup"] += 1
            continue

        # simhash 转载识别：仍入库（共振=热度信号），duplicate_of_id 指向首发
        sim = dedupe.simhash(p["text"])
        dup_of = None
        if sim:
            for cid, csim in sim_pool:
                if dedupe.is_duplicate(sim, csim, settings.INTEL_SIMHASH_HAMMING):
                    dup_of = cid
                    break

        raw_path = _write_raw(p["content_hash"], it.content_html or it.title)
        published = it.published_at
        # 防前视铁律：available_at = max(published_at, ingested_at)。
        # 在 SQL 内用同一语句时钟计算（见 store.insert_document），Python 侧先算 now()
        # 会与 DB 的 ingested_at=now() 差几毫秒，导致 available_at < ingested_at。
        doc_id = store.insert_document({
            "source_id": src["id"],
            "external_id": it.external_id or None,
            "url": it.url or None,
            "canonical_url": p["canonical_url"] or None,
            "title": it.title,
            "content": p["text"],
            "content_hash": p["content_hash"],
            "raw_path": raw_path,
            "published_at": published,
            "author": it.author or None,
            "redline": json.dumps(redline.match_redline(it.title, it.summary)),
            "simhash": dedupe.to_signed64(sim) if sim else None,  # 无符号64位 → PG BIGINT 有符号
            "duplicate_of_id": dup_of,
        })
        if sim:
            sim_pool.append((doc_id, sim))  # 本轮后续文章也能判到它
        existing["eids"].add(it.external_id)
        existing["urls"].add(p["canonical_url"])
        existing["hashes"].add(p["content_hash"])

        stats["new"] += 1
        stats["doc_ids"].append(doc_id)
        if dup_of:
            stats["reposts"] += 1
        if published and (stats["last_item_at"] is None or published > stats["last_item_at"]):
            stats["last_item_at"] = published

    return stats


def run_rss_ingest() -> dict:
    """RSS 摄取一轮（同步，executor 调用）。返回汇总 dict（供 tasks 记日志/冒烟断言）"""
    sources = store.list_enabled_sources(source_type="rss")
    if not sources:
        logger.info("intel: 无 enabled 的 RSS 源", extra={"task": "intel_rss_poll"})
        return {"sources": 0, "fetched": 0, "new": 0, "dup": 0, "reposts": 0,
                "failed_sources": 0, "details": []}

    results = _fetch_all(
        sources,
        timeout=settings.INTEL_HTTP_TIMEOUT_SEC,
        limit=settings.INTEL_PER_SOURCE_LIMIT,
        workers=settings.INTEL_FETCH_WORKERS,
    )

    summary = {"sources": len(sources), "fetched": 0, "new": 0, "dup": 0,
               "reposts": 0, "failed_sources": 0, "details": []}
    for src in sources:
        result = results[src["id"]]
        if not result.ok:
            summary["failed_sources"] += 1
            store.insert_feed_health(src["id"], "failed", result.latency_ms,
                                     result.error, None)
            logger.warning(
                f"intel 源拉取失败 {src['name']}: {result.error}",
                extra={"task": "intel_rss_poll", "source": src["name"]},
            )
            continue
        try:
            stats = _ingest_one_source(src, result)
        except Exception as e:  # noqa: BLE001 — 单源入库失败不拖垮整轮
            summary["failed_sources"] += 1
            store.insert_feed_health(src["id"], "failed", result.latency_ms,
                                     f"{type(e).__name__}: {str(e)[:150]}", None)
            logger.error(f"intel 源入库失败 {src['name']}: {e}",
                         extra={"task": "intel_rss_poll", "source": src["name"]})
            continue

        # 成功但没拉到任何 item（空 feed / 全解析失败）= partial
        status = "ok" if stats["fetched"] > 0 else "partial"
        store.insert_feed_health(src["id"], status, result.latency_ms, None,
                                 stats["last_item_at"])
    summary["fetched"] += stats["fetched"]
    summary["new"] += stats["new"]
    summary["dup"] += stats["dup"]
    summary["reposts"] += stats["reposts"]
    summary["details"].append({"source": src["name"], "status": status, **stats})

    return summary


# --------------------------------------------------------------------------
# P4-1 人工投喂入口
# --------------------------------------------------------------------------
def ensure_manual_source(name: str) -> dict:
    """登记/复用一个 manual 源（intel.sources），返回 {id, name, source_type}。

    url 用合成键 ``manual://<name>`` 保证按名幂等 upsert；manual 源不参与 RSS 轮询
    （run_rss_ingest 只拉 source_type='rss'），由 run_manual_ingest 显式驱动。
    """
    sid = store.upsert_source({
        "source_type": "manual",
        "name": name,
        "url": f"manual://{name}",
        "credibility": "medium",
        "poll_interval_sec": 0,
        "robots_ok": True,
        "enabled": False,
    })
    return {"id": sid, "name": name, "source_type": "manual"}


def run_manual_ingest(target, *, source_name: str | None = None,
                      limit: int | None = None) -> dict:
    """人工投喂入口（P4-1）：URL 清单 / 导出文件 / 目录 → 走与 RSS 相同入库链路。

    target: .txt/.csv 链接清单 | .html/.htm/.mhtml/.txt/.md 单文件 | 目录
    source_name: intel.sources 里的 manual 源名（默认取目录名或文件名主干）

    落库后理解层（run_intel_understanding）对来源无差别，自动接管 抽取→画像→因子。
    """
    target = str(target)
    if source_name is None:
        p = Path(target)
        source_name = (p.parent.name if p.is_dir() else (p.stem or "manual"))
    src = ensure_manual_source(source_name)
    items = ManualSource(source_name).collect(target)
    if limit:
        items = items[:limit]
    result = FetchResult(source_name=source_name, ok=True, items=items)
    stats = _ingest_one_source(src, result)
    stats["source_id"] = src["id"]
    stats["failed"] = (not result.ok)
    logger.info(
        f"manual 投喂完成 source={source_name} 新增={stats['new']} 重复={stats['dup']} 转载={stats['reposts']}",
        extra={"task": "intel_manual_ingest"},
    )
    return stats


# --------------------------------------------------------------------------
# P4-2 微信半自动：目录 watch（~/intel-inbox/ 浏览器插件/剪藏落地自动入库）
# --------------------------------------------------------------------------
def ensure_wechat_source(name: str) -> dict:
    """登记/复用一个 wechat 源（intel.sources），返回 {id, name, source_type}。

    url 用合成键 ``wechat://<name>`` 保证按名幂等 upsert；与 manual 一样不参与 RSS 轮询，
    由 run_wechat_watch / run_wechat_ingest 显式驱动。source_type='wechat' 仅作溯源区分。
    """
    sid = store.upsert_source({
        "source_type": "wechat",
        "name": name,
        "url": f"wechat://{name}",
        "credibility": "medium",
        "poll_interval_sec": 0,
        "robots_ok": True,
        "enabled": False,
    })
    return {"id": sid, "name": name, "source_type": "wechat"}


def run_wechat_ingest(target, *, source_name: str | None = None,
                      limit: int | None = None) -> dict:
    """单文件/目录的微信投喂入口（与 run_manual_ingest 同构，仅 source_type=wechat）。

    target: 微信导出文件（.html/.htm/.mhtml/.txt/.md）或目录
    source_name: intel.sources 里的 wechat 源名（默认取目录名或文件名主干）

    落库后理解层（run_intel_understanding）对来源无差别，自动接管 抽取→画像→因子。
    """
    target = str(target)
    if source_name is None:
        p = Path(target)
        source_name = (p.parent.name if p.is_dir() else (p.stem or "wechat"))
    src = ensure_wechat_source(source_name)
    items = ManualSource(source_name).collect(target)
    if limit:
        items = items[:limit]
    result = FetchResult(source_name=source_name, ok=True, items=items)
    stats = _ingest_one_source(src, result)
    stats["source_id"] = src["id"]
    stats["failed"] = (not result.ok)
    logger.info(
        f"wechat 投喂完成 source={source_name} 新增={stats['new']} 重复={stats['dup']} 转载={stats['reposts']}",
        extra={"task": "intel_wechat_ingest"},
    )
    return stats


def _wechat_inbox_candidates(inbox: Path, cooldown_sec: float) -> list[Path]:
    """扫描 inbox 下待处理文件：排除 processed/ 子目录、排除不支持扩展名、
    跳过 mtime 距现在 < cooldown_sec 的文件（剪藏工具正在写入）。
    """
    now = time.time()
    out: list[Path] = []
    for f in inbox.rglob("*"):
        if not f.is_file():
            continue
        if "processed" in f.parts:        # 已归档 → 跳过
            continue
        if f.suffix.lower() not in _SUPPORTED_EXT:
            continue
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        age = now - mtime
        if age < 0:                       # 文件系统/时钟分辨率导致 mtime 略超前 now → 视为刚写完
            age = 0
        if age < cooldown_sec:            # 正在被写入，下一轮再处理
            continue
        out.append(f)
    out.sort(key=lambda p: (p.stat().st_mtime, str(p)))
    return out


def run_wechat_watch(*, inbox: str | Path | None = None, once: bool = True,
                     default_source: str | None = None,
                     cooldown_sec: float | None = None,
                     move_processed: bool = True) -> dict:
    """目录 watch 主循环（P4b）：监听 ~/intel-inbox/，新文件按**子目录=公众号**分组入库，
    处理完移入 processed/ 作幂等标记（重跑只捡新文件；同内容因 content_hash 判重不重复入库）。

    - inbox: 监听目录（默认 settings.INTEL_INBOX_DIR = ~/intel-inbox）
    - once: True 扫描一轮即返回；False 则每 INTEL_INBOX_POLL_SEC 秒循环（由 tasks 经 executor 调）
    - 扁平文件（inbox 根下、无子目录）→ 归入 default_source（默认 settings.INTEL_INBOX_DEFAULT_SOURCE）
    - 子目录文件 → 源名取子目录名（一个公众号一个源，画像/因子溯源更干净）
    返回汇总 dict（供 tasks 记日志/冒烟断言）。单文件失败不拖垮整轮（留原地等下轮重试）。
    """
    inbox = Path(inbox or settings.INTEL_INBOX_DIR).expanduser()
    if not inbox.exists():
        logger.warning(f"intel-inbox 不存在，跳过 watch: {inbox}",
                       extra={"task": "intel_wechat_watch"})
        return {"inbox": str(inbox), "exists": False, "files": 0, "new": 0,
                "dup": 0, "reposts": 0, "errors": 0, "details": []}
    cooldown_sec = cooldown_sec if cooldown_sec is not None else settings.INTEL_INBOX_COOLDOWN_SEC
    default_source = default_source or settings.INTEL_INBOX_DEFAULT_SOURCE
    processed_dir = inbox / "processed"

    summary: dict = {"inbox": str(inbox), "exists": True, "files": 0, "new": 0,
                     "dup": 0, "reposts": 0, "errors": 0, "details": []}

    while True:
        candidates = _wechat_inbox_candidates(inbox, cooldown_sec)
        summary["files"] += len(candidates)
        for f in candidates:
            rel = f.relative_to(inbox)
            # 子目录名 = 公众号源名；根下扁平文件 = 默认源
            src_name = rel.parts[0] if len(rel.parts) > 1 else default_source
            try:
                stats = run_wechat_ingest(f, source_name=src_name)
            except Exception as e:  # noqa: BLE001 — 单文件失败不拖垮整轮
                summary["errors"] += 1
                logger.error(
                    f"wechat watch 入库失败 {f}: {type(e).__name__}: {str(e)[:150]}",
                    extra={"task": "intel_wechat_watch"},
                )
                continue  # 留原地，下轮重试
            summary["new"] += stats["new"]
            summary["dup"] += stats["dup"]
            summary["reposts"] += stats["reposts"]
            summary["details"].append({
                "file": str(f), "source": src_name, **stats,
            })
            # 入库成功 → 移入 processed/（保留子目录结构），作幂等标记
            if move_processed:
                dest = processed_dir / rel
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if dest.exists():  # 同名碰撞：追加 mtime 避免覆盖
                        dest = dest.with_name(f"{dest.stem}.{int(f.stat().st_mtime)}{dest.suffix}")
                    shutil.move(str(f), str(dest))
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"wechat watch 归档失败 {f}: {e}")

        if once:
            break
        time.sleep(settings.INTEL_INBOX_POLL_SEC)

    logger.info(
        f"wechat watch 完成 files={summary['files']} new={summary['new']} "
        f"dup={summary['dup']} errors={summary['errors']}",
        extra={"task": "intel_wechat_watch"},
    )
    return summary


# --------------------------------------------------------------------------
# P4-3 RSSHub 可选源（默认 disabled，health 标 degraded）
# --------------------------------------------------------------------------
def ensure_rsshub_source(name: str, url: str, *, enabled: bool = False,
                         credibility: str = "low") -> dict:
    """登记/复用一个 rsshub 源（intel.sources），返回 {id, name, source_type}。

    url 写 ``rsshub://<route>``（按 settings.INTEL_RSSHUB_BASE_URL 解析）或完整 feed URL。
    **默认 enabled=False**：rsshub 是第三方中继，不自动开；仅用户显式 enable 后才被拉取。
    """
    sid = store.upsert_source({
        "source_type": "rsshub",
        "name": name,
        "url": url,
        "credibility": credibility,
        "poll_interval_sec": 0,
        "robots_ok": True,
        "enabled": enabled,
    })
    return {"id": sid, "name": name, "source_type": "rsshub"}


def run_rsshub_ingest() -> dict:
    """RSSHub 摄取一轮（与 run_rss_ingest 同构，但 source_type='rsshub'）。

    仅拉**已 enabled** 的 rsshub 源（默认无，需用户显式开启）。
    成功拉取后 feed_health 记 **degraded**——第三方中继，不假装稳；拉取失败记 failed。
    返回汇总 dict。
    """
    sources = store.list_enabled_sources(source_type="rsshub")
    if not sources:
        logger.info("intel: 无 enabled 的 RSSHub 源", extra={"task": "intel_rsshub_poll"})
        return {"sources": 0, "fetched": 0, "new": 0, "dup": 0, "reposts": 0,
                "failed_sources": 0, "details": []}

    results = _fetch_all(
        sources,
        timeout=settings.INTEL_HTTP_TIMEOUT_SEC,
        limit=settings.INTEL_PER_SOURCE_LIMIT,
        workers=settings.INTEL_FETCH_WORKERS,
    )

    summary = {"sources": len(sources), "fetched": 0, "new": 0, "dup": 0,
               "reposts": 0, "failed_sources": 0, "details": []}
    for src in sources:
        result = results[src["id"]]
        if not result.ok:
            summary["failed_sources"] += 1
            # 失败也标 degraded：第三方源本就不可靠，但明确失败
            store.insert_feed_health(src["id"], "failed", result.latency_ms,
                                     result.error, None)
            logger.warning(
                f"intel RSSHub 源拉取失败 {src['name']}: {result.error}",
                extra={"task": "intel_rsshub_poll", "source": src["name"]},
            )
            continue
        try:
            stats = _ingest_one_source(src, result)
        except Exception as e:  # noqa: BLE001 — 单源入库失败不拖垮整轮
            summary["failed_sources"] += 1
            store.insert_feed_health(src["id"], "failed", result.latency_ms,
                                     f"{type(e).__name__}: {str(e)[:150]}", None)
            logger.error(f"intel RSSHub 源入库失败 {src['name']}: {e}",
                         extra={"task": "intel_rsshub_poll", "source": src["name"]})
            continue

        status = "ok" if stats["fetched"] > 0 else "partial"
        # P4-3 核心：即便本轮回拉取成功，也如实标 degraded（第三方中继，不假装稳）
        store.insert_feed_health(src["id"], "degraded", result.latency_ms, None,
                                 stats["last_item_at"])
        summary["fetched"] += stats["fetched"]
        summary["new"] += stats["new"]
        summary["dup"] += stats["dup"]
        summary["reposts"] += stats["reposts"]
        summary["details"].append({"source": src["name"], "status": status, **stats})

    return summary
