"""intel 存储层（同步 SQLAlchemy + psycopg2，与 dc raw_store 同款）

executor 里的摄取闭环直写 intel.* 表，不碰 async 引擎 / run_async，
避免 asyncpg 连接池绑错 loop 的坑（见 app/storage/db.py 顶部说明）。
API 侧（主请求循环）读 intel 表走 app.intel.core.db 的 async session。
"""
from __future__ import annotations

import json
import re

from sqlalchemy import create_engine, text

from app.intel.core.config import settings
from app.intel.core.logging import get_logger

logger = get_logger(__name__)

_engine = None


def _sync_url() -> str:
    """DATABASE_URL 的 asyncpg driver 降级为 psycopg2（同步写，不影响主 async 用法）"""
    url = settings.DATABASE_URL
    if "+asyncpg" in url:
        url = url.replace("+asyncpg", "+psycopg2")
    elif not url.startswith("postgresql+psycopg2") and url.startswith("postgresql"):
        url = url.replace("postgresql", "postgresql+psycopg2", 1)
    return url


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(_sync_url(), pool_pre_ping=True, future=True)
    return _engine


def run_sql_file(path: str, engine=None) -> None:
    """原生执行一个 .sql 迁移文件（不经过 SQLAlchemy 的 bind 参数解析）。

    迁移文件里的 SQL 注释常含 "冒号+字母/数字"（如 `-- {"count" = 8}` 写错成
    `{"count":8}`），经 `text()` 执行会被当成 bind parameter，报
    "A value is required for bind parameter '8'"，且因是 DDL 里混在注释中极难察觉。

    这里用 DBAPI cursor 直接执行，完全绕开该解析，迁移文件可放心写注释。
    """
    if engine is None:
        engine = get_engine()
    with open(path, "r", encoding="utf-8") as f:
        sql = f.read()

    raw = engine.raw_connection()
    try:
        conn = getattr(raw, "driver_connection", raw)
        cur = conn.cursor()
        cur.execute(sql)
        cur.close()
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def list_enabled_sources(source_type: str | None = None) -> list[dict]:
    sql = (
        "SELECT id, source_type, name, url, poll_interval_sec "
        "FROM intel.sources WHERE enabled = true"
    )
    params: dict = {}
    if source_type:
        sql += " AND source_type = :st"
        params["st"] = source_type
    with get_engine().connect() as c:
        rows = c.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def existing_document_keys(
    source_id: int,
    external_ids: list[str],
    canonical_urls: list[str],
    content_hashes: list[str],
) -> dict[str, set]:
    """批量预查三级幂等键：同源 external_id / canonical_url / content_hash（各自去重集合）"""
    out: dict[str, set] = {"eids": set(), "urls": set(), "hashes": set()}
    with get_engine().connect() as c:
        if external_ids:
            rows = c.execute(
                text(
                    "SELECT external_id FROM intel.documents "
                    "WHERE source_id = :sid AND external_id = ANY(:ids)"
                ),
                {"sid": source_id, "ids": external_ids},
            ).scalars().all()
            out["eids"] = set(rows)
        if canonical_urls:
            rows = c.execute(
                text(
                    "SELECT canonical_url FROM intel.documents "
                    "WHERE canonical_url = ANY(:urls)"
                ),
                {"urls": canonical_urls},
            ).scalars().all()
            out["urls"] = set(rows)
        if content_hashes:
            rows = c.execute(
                text(
                    "SELECT content_hash FROM intel.documents "
                    "WHERE content_hash = ANY(:hashes)"
                ),
                {"hashes": content_hashes},
            ).scalars().all()
            out["hashes"] = set(rows)
    return out


def simhash_candidates(window_days: int) -> list[tuple[int, int]]:
    """近 window_days 天入库文档的 (id, simhash)，转载识别比对池（不含 simhash=0 的短文本）

    库里存的是有符号 64 位（PG BIGINT），读出转回无符号域参与汉明比较。
    """
    from app.intel.normalize import dedupe

    with get_engine().connect() as c:
        rows = c.execute(
            text(
                "SELECT id, simhash FROM intel.documents "
                "WHERE ingested_at >= now() - make_interval(days => :days) "
                "AND simhash IS NOT NULL AND simhash <> 0"
            ),
            {"days": window_days},
        ).all()
    return [(int(r[0]), dedupe.to_unsigned64(int(r[1]))) for r in rows]


def insert_document(doc: dict) -> int:
    """插入一篇文档，返回 id。doc 键与 intel.documents 列对应（redline 传 JSON 字符串）

    防前视：available_at 在 SQL 内用与 ingested_at 相同的 now() 计算
    （GREATEST(COALESCE(published, now()), now())），保证 available_at >= ingested_at
    精确成立——Python 侧预计算 now() 会与 DB 时钟差几毫秒导致违例。
    """
    with get_engine().begin() as c:
        row = c.execute(
            text(
                """
                INSERT INTO intel.documents
                    (source_id, external_id, url, canonical_url, title, content,
                     content_hash, raw_path, published_at, ingested_at, available_at,
                     author, redline, simhash, duplicate_of_id)
                VALUES
                    (:source_id, :external_id, :url, :canonical_url, :title, :content,
                     :content_hash, :raw_path, :published_at, now(),
                     GREATEST(COALESCE(:published_at, now()), now()),
                     :author, CAST(:redline AS jsonb), :simhash, :duplicate_of_id)
                RETURNING id
                """
            ),
            doc,
        ).scalar_one()
    return int(row)


def insert_feed_health(
    source_id: int, status: str, latency_ms: int, error: str | None, last_item_at
) -> None:
    with get_engine().begin() as c:
        c.execute(
            text(
                """
                INSERT INTO intel.feed_health
                    (source_id, status, latency_ms, error, last_item_at)
                VALUES (:sid, :status, :lat, :err, :last_item)
                """
            ),
            {
                "sid": source_id,
                "status": status,
                "lat": latency_ms,
                "err": error,
                "last_item": last_item_at,
            },
        )


def upsert_source(src: dict) -> int:
    """seed 用：按 url 幂等 upsert 一个源，返回 id"""
    with get_engine().begin() as c:
        row = c.execute(
            text(
                """
                INSERT INTO intel.sources
                    (source_type, name, url, credibility, poll_interval_sec, robots_ok, enabled)
                VALUES
                    (:source_type, :name, :url, :credibility, :poll_interval_sec, :robots_ok, :enabled)
                ON CONFLICT (url) DO UPDATE SET
                    name = EXCLUDED.name,
                    source_type = EXCLUDED.source_type,
                    credibility = EXCLUDED.credibility,
                    poll_interval_sec = EXCLUDED.poll_interval_sec
                RETURNING id
                """
            ),
            src,
        ).scalar_one()
    return int(row)


# --------------------------------------------------------------------------
# 源管理（P4-3 RSSHub 前端管理页）
#
# upsert_source 的 ON CONFLICT (url) **刻意不更新 enabled**：批量 seed 时不应
# 把用户手工停掉的源重新打开。因此启用/停用/改名/改址都要走这里的显式 UPDATE。
# --------------------------------------------------------------------------
def count_documents_by_source(source_id: int) -> int:
    """该源下已入库文档数（删除前的守卫：有文档的源不允许删，避免破坏溯源）"""
    with get_engine().connect() as c:
        return int(
            c.execute(
                text("SELECT count(*) FROM intel.documents WHERE source_id = :sid"),
                {"sid": source_id},
            ).scalar_one()
        )


def update_source(
    source_id: int,
    *,
    name: str | None = None,
    url: str | None = None,
    enabled: bool | None = None,
    credibility: str | None = None,
) -> bool:
    """按 id 局部更新源（只动传入的字段），返回是否命中行

    url 变更会撞 sources.url 唯一约束，由调用方先把冲突转成 409；这里不吞异常，
    让 HTTP 层看到 IntegrityError 以便给出"该 URL 已被别的源占用"的明确提示。
    """
    sets: list[str] = []
    params: dict = {"sid": source_id}
    if name is not None:
        sets.append("name = :name")
        params["name"] = name
    if url is not None:
        sets.append("url = :url")
        params["url"] = url
    if enabled is not None:
        sets.append("enabled = :enabled")
        params["enabled"] = bool(enabled)
    if credibility is not None:
        sets.append("credibility = :credibility")
        params["credibility"] = credibility
    if not sets:
        return False

    with get_engine().begin() as c:
        res = c.execute(
            text(f"UPDATE intel.sources SET {', '.join(sets)} WHERE id = :sid"), params
        )
        return bool(res.rowcount)


def delete_source(source_id: int) -> bool:
    """删除源（仅当其下无文档；有文档的源请先禁用——原文与 doc_mentions 都要保留溯源）"""
    with get_engine().begin() as c:
        res = c.execute(text("DELETE FROM intel.sources WHERE id = :sid"), {"sid": source_id})
        return bool(res.rowcount)


# ---- 理解层（P1）：doc_mentions / doc_style / llm_runs / quarantine ----

def insert_mention(m: dict) -> int:
    """写一条 doc_mentions（span 已在 service 层校验过才允许进到这里）"""
    with get_engine().begin() as c:
        row = c.execute(
            text(
                """
                INSERT INTO intel.doc_mentions
                    (doc_id, symbol, stance, confidence, horizon, thesis,
                     evidence, span_start, span_end, method, prompt_version, llm_version)
                VALUES
                    (:doc_id, :symbol, :stance, :confidence, :horizon, :thesis,
                     :evidence, :span_start, :span_end, :method, :prompt_version, :llm_version)
                RETURNING id
                """
            ),
            m,
        ).scalar_one()
    return int(row)


def insert_style(doc_id: int, style_dims: dict, prompt_version: str, llm_version: str) -> int:
    with get_engine().begin() as c:
        row = c.execute(
            text(
                """
                INSERT INTO intel.doc_style
                    (doc_id, style_dims, prompt_version, llm_version)
                VALUES
                    (:doc_id, CAST(:dims AS jsonb), :pv, :lv)
                RETURNING id
                """
            ),
            {"doc_id": doc_id, "dims": json.dumps(style_dims),
             "pv": prompt_version, "lv": llm_version},
        ).scalar_one()
    return int(row)


def insert_llm_run(run: dict) -> int:
    with get_engine().begin() as c:
        row = c.execute(
            text(
                """
                INSERT INTO intel.llm_runs
                    (doc_id, provider, model, prompt_version, input_tokens,
                     output_tokens, cost_cny, latency_ms, status, error)
                VALUES
                    (:doc_id, :provider, :model, :prompt_version, :input_tokens,
                     :output_tokens, :cost_cny, :latency_ms, :status, :error)
                RETURNING id
                """
            ),
            run,
        ).scalar_one()
    return int(row)


def insert_quarantine(doc_id: int | None, stage: str, reason: str, payload: dict) -> int:
    """被拒输出落隔离区（P1-Gate 复盘素材），附原因"""
    with get_engine().begin() as c:
        row = c.execute(
            text(
                """
                INSERT INTO intel.quarantine (doc_id, stage, reason, payload)
                VALUES (:doc_id, :stage, :reason, CAST(:payload AS jsonb))
                RETURNING id
                """
            ),
            {"doc_id": doc_id, "stage": stage, "reason": reason,
             "payload": json.dumps(payload, ensure_ascii=False, default=str)},
        ).scalar_one()
    return int(row)


def llm_spent_today_cny() -> float:
    """当日（北京时区自然日）已花的 LLM 成本——预算闸依据"""
    with get_engine().connect() as c:
        val = c.execute(
            text(
                """
                SELECT COALESCE(SUM(cost_cny), 0) FROM intel.llm_runs
                WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai')
                    AT TIME ZONE 'Asia/Shanghai'
                  AND status <> 'budget_skip'
                """
            )
        ).scalar()
    return float(val)


def _extract_priority_map() -> dict[str, int]:
    """解析 INTEL_EXTRACT_PRIORITY（形如 "manual:10,wechat:20,rss:50"）。

    返回 source_type → 优先级（小者先抽）。解析失败/未配置时返回空 dict，
    调用方退化为「纯 id 升序」（即改动前的行为）。
    """
    raw = getattr(settings, "INTEL_EXTRACT_PRIORITY", "") or ""
    out: dict[str, int] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, _, val = part.partition(":")
        key = key.strip().lower()
        if not key:  # ":9" 这类只有冒号的畸形项，跳过（空 key 会污染 CASE 分支）
            continue
        try:
            out[key] = int(val.strip())
        except ValueError:
            continue
    return out


def _understanding_order_sql(prio: dict[str, int]) -> tuple[str, dict]:
    """生成待理解文档的 ORDER BY 子句与绑定参数（纯函数，便于单测）

    三层（D-3 固化）：
      1. ``source_type`` 优先级（``manual``/``wechat`` 先于 ``rss``）；
      2. 同级内 ``available_at DESC``（先理解新鲜的，时效性对资讯有意义）；
      3. ``d.id`` 兜底，保证稳定排序 —— 分页不重不漏。

    空 ``prio`` → 返回 ``ORDER BY d.id``（即改动前行为，可作回滚开关）。
    """
    if not prio:
        return "ORDER BY d.id", {}
    # 统一小写 + 参数化 + 白名单校验：配置内容绝不直接拼进 SQL
    safe = {k.lower(): v for k, v in prio.items()
            if re.fullmatch(r"[a-z0-9_]+", k.lower())}
    if not safe:
        return "ORDER BY d.id", {}
    cases = " ".join(f"WHEN :p_{k} THEN {int(v)}" for k, v in safe.items())
    params = {f"p_{k}": k for k in safe}
    # available_at 可能为 NULL（RSS 源未给发布时间）→ NULLS LAST 让它们沉到同组末尾
    # 而不是被 PG 默认的 DESC(NULLS FIRST) 顶到最前
    return (
        f"ORDER BY CASE LOWER(s.source_type) {cases} ELSE 100 END, "
        f"d.available_at DESC NULLS LAST, d.id"
    ), params


def docs_for_understanding(limit: int, prompt_version: str | None = None,
                           doc_ids: list[int] | None = None) -> list[dict]:
    """待理解文档：预筛由调用方做；这里给出未做过 LLM 抽取的候选（含标题与正文）

    已处理口径：存在**当前 prompt_version** 的 ok 级 llm_run（含 no_mention 与
    quarantine——分别代表"确实无标的"与"待人工审"的重跑都没有意义）。
    api_fail 的文档没有 ok run，天然会被重试。

    doc_ids: 只取这批 id（上传后立即抽取用，避免把历史欠账一起抽了）。

    排序（D-3 固化）：**不再按 ``d.id`` 升序**。入库 id 大小只反映"抓取先后"，
    与"是否值得先理解"无关 —— 此前 RSS 老欠账 id 小永远先抽；用户手动投喂的
    公众号文章 id 大，排在几千条之后饿死。现按三层（见 ``_understanding_order_sql``）：
    source_type 优先级 → available_at DESC → d.id。
    """
    pv = prompt_version
    if pv is None:
        from app.intel.understand.prompts import PROMPT_VERSION
        pv = PROMPT_VERSION
    scope = "AND d.id = ANY(:ids)" if doc_ids else ""
    params: dict = {"lim": limit, "pv": pv}
    if doc_ids:
        params["ids"] = [int(i) for i in doc_ids]

    if doc_ids:
        # 定向抽取：调用方已给定关心的顺序，且这批量本该独立于全局优先级
        order_by, join = "ORDER BY d.id", ""
    else:
        order_by, extra = _understanding_order_sql(_extract_priority_map())
        params.update(extra)
        join = "JOIN intel.sources s ON s.id = d.source_id" if extra else ""

    with get_engine().connect() as c:
        rows = c.execute(
            text(
                f"""
                SELECT d.id, d.title, d.content, d.available_at
                FROM intel.documents d
                {join}
                WHERE NOT EXISTS (
                    SELECT 1 FROM intel.llm_runs r
                    WHERE r.doc_id = d.id AND r.status = 'ok'
                      AND r.prompt_version = :pv
                )
                {scope}
                {order_by}
                LIMIT :lim
                """
            ),
            params,
        ).mappings().all()
    return [dict(r) for r in rows]
