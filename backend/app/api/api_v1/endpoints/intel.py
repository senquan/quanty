"""资讯分析 intel 数据接口

读 Postgres ``intel`` schema（由 data-cleaner 产出），暴露只读端点：
- GET /api/v1/intel/mentions  资讯抽取结果（doc_mentions JOIN documents + sources，**服务端分页**）
- GET /api/v1/intel/profiles  作者/来源画像（author_profiles）
- /api/v1/intel/rsshub/*      RSSHub 源管理（转发 dc，P4-3）

字段一律以 camelCase 返回，前端 news-analysis 组件可直接消费，无需转换。
"""
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.user import User
from app.schemas.response import Response

router = APIRouter(prefix="/intel", tags=["intel"])

# 只暴露经过 P1-Gate 校验的 v2 产出，避免旧版本噪声污染前端
PROMPT_VERSION = "v2"
# 分页：前端资讯抽取页 20 行一页。数据量随每日构建持续增长（已 800+ 条），
# 全量下发既拖慢首屏也让前端筛选失真（只筛得到已下载的那一页），故筛选与分页
# 一律下推到 SQL，前端只渲染当前页。
MENTION_DEFAULT_PAGE_SIZE = 20
MENTION_MAX_PAGE_SIZE = 100


def _mention_where(q: str, stance: str, source: str) -> tuple[str, dict]:
    """mentions 的筛选条件（列表与 count 共用，避免两处 SQL 漂移）"""
    where = "WHERE m.prompt_version = :pv"
    params: dict = {"pv": PROMPT_VERSION}
    if q:
        where += (
            " AND (d.title ILIKE :like OR m.thesis ILIKE :like"
            " OR m.evidence ILIKE :like OR m.symbol ILIKE :like)"
        )
        params["like"] = f"%{q}%"
    if stance and stance != "all":
        where += " AND m.stance = :stance"
        params["stance"] = stance
    if source and source != "all":
        where += " AND s.name = :source"
        params["source"] = source
    return where, params


_MENTION_ROW_SQL = """
    FROM intel.doc_mentions m
    JOIN intel.documents d ON d.id = m.doc_id
    LEFT JOIN intel.sources s ON s.id = d.source_id
"""


@router.get("/mentions", summary="资讯抽取结果（分页）")
async def list_mentions(
    q: str = "",
    stance: str = "all",
    source: str = "all",
    page: int = 1,
    pageSize: int = MENTION_DEFAULT_PAGE_SIZE,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """分页返回资讯抽取结果

    - ``q``     关键字，匹配标题 / 论点 / 证据 / 标的
    - ``stance`` bullish | neutral | bearish | all
    - ``source`` 源名（下拉选项由本接口 ``sources`` 字段给出）| all
    - ``page`` / ``pageSize`` 1-based 分页，pageSize 上限 100
    """
    page = max(1, page)
    size = max(1, min(pageSize, MENTION_MAX_PAGE_SIZE))
    where, params = _mention_where(q, stance, source)

    total = int(
        (
            await db.execute(
                text(f"SELECT count(*) {_MENTION_ROW_SQL} {where}"), params
            )
        ).scalar_one()
    )

    list_params = {**params, "lim": size, "off": (page - 1) * size}
    rows = (
        await db.execute(
            text(
                "SELECT m.id, m.doc_id, d.title, m.symbol, m.stance, m.confidence,"
                " m.horizon, m.thesis, m.evidence, d.author,"
                " s.name AS source_name, d.published_at"
                f" {_MENTION_ROW_SQL} {where}"
                " ORDER BY d.published_at DESC NULLS LAST, m.id DESC"
                " LIMIT :lim OFFSET :off"
            ),
            list_params,
        )
    ).mappings().all()

    items = [
        {
            "id": str(r["id"]),
            "docId": str(r["doc_id"]),
            "title": r["title"] or "",
            "symbol": r["symbol"],
            "stance": r["stance"],
            "confidence": float(r["confidence"]) if r["confidence"] is not None else 0.0,
            "horizon": r["horizon"] or "event",
            "thesis": r["thesis"] or "",
            "evidence": r["evidence"] or "",
            "source": r["source_name"] or "",
            "author": r["author"],
            "publishedAt": r["published_at"].isoformat() if r["published_at"] else "",
        }
        for r in rows
    ]

    # 来源下拉不随分页/筛选变动（否则第二页起下拉会只剩当前页的来源）
    src_rows = (
        await db.execute(
            text(
                "SELECT DISTINCT s.name FROM intel.sources s"
                " JOIN intel.documents d ON d.source_id = s.id"
                " JOIN intel.doc_mentions m ON m.doc_id = d.id"
                " WHERE m.prompt_version = :pv AND s.name IS NOT NULL"
                " ORDER BY s.name"
            ),
            {"pv": PROMPT_VERSION},
        )
    ).scalars().all()

    return Response.success(
        data={
            "items": items,
            "total": total,
            "page": page,
            "pageSize": size,
            "sources": [s for s in src_rows if s],
        }
    )


@router.get("/profiles", summary="作者/来源画像")
async def list_profiles(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    # 画像版本（profile_version）独立于理解层 prompt_version（当前为 v1 基线），
    # 故不按字面量过滤，而是取每个 profile_key 最新 computed_at 的版本，
    # 未来 P2 重建产生新版本时接口自动展示最新画像。
    # P4-4：LEFT JOIN LATERAL 取每位作者最新一条 LLM 风格总结（migrations/016），
    # 未生成总结时风格字段为 null，前端可不渲染。
    sql = text(
        """
        SELECT p.profile_key, p.profile_type, p.total_mentions, p.total_docs,
               p.unique_symbols, p.stance_dist, p.top_symbols, p.date_first,
               p.date_last, p.sample_insufficient, p.accuracy_sample_size,
               p.drift_detected,
               ss.summary       AS style_summary,
               ss.style_tags    AS style_tags,
               ss.sectors       AS style_sectors,
               ss.holding_period AS style_holding_period,
               ss.conviction    AS style_conviction,
               ss.caveats       AS style_caveats,
               ss.confidence    AS style_confidence,
               ss.status        AS style_status
        FROM intel.author_profiles p
        LEFT JOIN LATERAL (
            SELECT summary, style_tags, sectors, holding_period, conviction,
                   caveats, confidence, status
            FROM intel.author_style_summaries s
            WHERE s.profile_key = p.profile_key
              AND s.profile_type = p.profile_type
            ORDER BY s.computed_at DESC
            LIMIT 1
        ) ss ON true
        WHERE (p.profile_key, p.computed_at) IN (
            SELECT profile_key, max(computed_at)
            FROM intel.author_profiles
            GROUP BY profile_key
        )
        ORDER BY p.total_mentions DESC
        """
    )
    res = await db.execute(sql)
    rows = res.mappings().all()
    data = []
    for r in rows:
        sd = r["stance_dist"] or {}
        top = r["top_symbols"] or []
        data.append(
            {
                "profileKey": r["profile_key"],
                "profileType": r["profile_type"],
                "totalMentions": r["total_mentions"] or 0,
                "totalDocs": r["total_docs"] or 0,
                "uniqueSymbols": r["unique_symbols"] or 0,
                "stanceDist": {
                    "bullish": int(sd.get("bullish", 0) or 0),
                    "neutral": int(sd.get("neutral", 0) or 0),
                    "bearish": int(sd.get("bearish", 0) or 0),
                },
                "topSymbols": [
                    s["symbol"]
                    for s in top
                    if isinstance(s, dict) and s.get("symbol")
                ],
                "dateFirst": r["date_first"].isoformat() if r["date_first"] else "",
                "dateLast": r["date_last"].isoformat() if r["date_last"] else "",
                "sampleInsufficient": bool(r["sample_insufficient"]),
                "accuracySampleSize": r["accuracy_sample_size"] or 0,
                "driftDetected": bool(r["drift_detected"]),
                # P4-4 风格总结（LLM 产出；未生成时为 null / 空）
                "styleSummary": r["style_summary"] or "",
                "styleTags": list(r["style_tags"] or []),
                "styleSectors": list(r["style_sectors"] or []),
                "styleHoldingPeriod": r["style_holding_period"] or "",
                "styleConviction": r["style_conviction"] or "",
                "styleCaveats": r["style_caveats"] or "",
                "styleConfidence": (
                    float(r["style_confidence"])
                    if r["style_confidence"] is not None
                    else None
                ),
                "styleStatus": r["style_status"] or "",
            }
        )
    return Response.success(data=data)


# --------------------------------------------------------------------------
# P4-1 批量上传（人工投喂）
# --------------------------------------------------------------------------
# 解析实现（ManualSource：html/htm/mhtml 走 bs4、mhtml 走 email、txt/md 纯文本、
# txt/csv 当 URL 清单；zip 在 dc 侧自动解压）在 data-cleaner 侧，这里只做代转发：
# 架构约定前端不直连 dc，统一经主后端转发（见 frontend vite.config.ts 注释）。
# 注意：本白名单必须与 dc 侧 app/intel/api.py::UPLOAD_ALLOWED_EXT 保持一致，
# 否则会出现"网关放行、dc 拒收"或"网关就拦了、功能形同没有"。
UPLOAD_ALLOWED_EXT = {".html", ".htm", ".mhtml", ".txt", ".md", ".csv", ".zip"}
UPLOAD_MAX_FILES = 200
UPLOAD_MAX_BYTES = 20 * 1024 * 1024
UPLOAD_TIMEOUT_SEC = 300.0  # 批量解析 + 入库可能较慢
# 开着 extract 时 dc 会同步跑 LLM 抽取（实测 3~4s/篇，并发 4），50 篇约 1 分钟，
# 网关超时必须比它宽，否则前端拿到 504 而 dc 其实还在好好干活。
UPLOAD_EXTRACT_TIMEOUT_SEC = 900.0


@router.post("/upload", summary="批量上传文章文件（转发 data-cleaner 入库）")
async def upload_articles(
    files: list[UploadFile] = File(
        ..., description="文章文件：html/htm/mhtml/txt/md/csv，或打包的 zip（dc 侧自动解压）"
    ),
    source_name: str = Form("", description="源名（如公众号名）；留空为『上传』"),
    source_type: str = Form("manual", description="manual | wechat，仅影响溯源标记"),
    dry_run: bool = Form(False, description="只解析不写库"),
    extract: bool = Form(False, description="入库后立即对本次新增文档跑理解层抽取（花钱）"),
    extract_limit: int = Form(0, description="立即抽取篇数上限；0 或不传 = 用 dc 默认值"),
    _user: User = Depends(get_current_user),
):
    """前端批量投喂入口：转发到 dc 的 POST /api/v1/intel/upload

    ``extract=true`` 时 dc 会在入库后立刻对**本次新增**的文档跑理解层抽取
    （预筛 → LLM → doc_mentions），省掉再登服务器敲脚本；抽取结果在响应的
    ``extraction`` 字段。LLM 不可用不影响上传结果，只在 extraction 里说明原因。
    """
    import httpx

    if not files:
        return Response.fail(code=400, msg="未选择文件")
    if len(files) > UPLOAD_MAX_FILES:
        return Response.fail(
            code=400, msg=f"一次最多 {UPLOAD_MAX_FILES} 个文件，当前 {len(files)} 个"
        )
    if source_type not in {"manual", "wechat"}:
        return Response.fail(code=400, msg="source_type 只能是 manual 或 wechat")

    payload: list[tuple] = []
    rejected: list[dict] = []
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in UPLOAD_ALLOWED_EXT:
            rejected.append({"file": f.filename, "error": f"不支持的扩展名 {ext or '(无)'}"})
            continue
        data = await f.read()
        if len(data) > UPLOAD_MAX_BYTES:
            rejected.append({
                "file": f.filename,
                "error": f"超过单文件上限 {UPLOAD_MAX_BYTES // 1024 // 1024}MB",
            })
            continue
        payload.append(
            ("files", (f.filename, data, f.content_type or "application/octet-stream"))
        )

    if not payload:
        return Response.fail(
            code=400, msg="没有可导入的文件：" + "；".join(r["error"] for r in rejected[:3])
        )

    url = f"{settings.INTEL_CLEANER_BASE_URL.rstrip('/')}/api/v1/intel/upload"
    headers = (
        {"X-API-Key": settings.INTEL_CLEANER_API_KEY}
        if settings.INTEL_CLEANER_API_KEY
        else {}
    )
    form = {
        "source_name": source_name,
        "source_type": source_type,
        "dry_run": "true" if dry_run else "false",
        "extract": "true" if extract else "false",
    }
    if extract_limit > 0:
        form["extract_limit"] = str(extract_limit)

    try:
        # 开着 extract 时 dc 会同步跑 LLM（实测 3~4s/篇，并发 4），超时给到 900s
        timeout = UPLOAD_EXTRACT_TIMEOUT_SEC if extract else UPLOAD_TIMEOUT_SEC
        async with httpx.AsyncClient(timeout=timeout) as cli:
            resp = await cli.post(url, files=payload, data=form, headers=headers)
    except httpx.HTTPError as e:
        return Response.fail(code=502, msg=f"无法连接 data-cleaner（{url}）：{e}")

    if resp.status_code != 200:
        detail = ""
        try:
            detail = (resp.json() or {}).get("detail", "")
        except Exception:  # noqa: BLE001
            detail = resp.text[:200]
        return Response.fail(code=resp.status_code, msg=f"data-cleaner 拒绝：{detail}")

    body = resp.json()
    if rejected:  # 本层拒收的也要回给前端，避免"丢了文件却没提示"
        body["rejected"] = rejected + body.get("rejected", [])
        body["failed"] = body.get("failed", 0) + len(rejected)
    return Response.success(data=body)


# --------------------------------------------------------------------------
# P4-3 RSSHub 源管理（转发 data-cleaner）
# --------------------------------------------------------------------------
# 与 P4-1 上传同理：前端不直连 dc，一律经主后端转发。RSSHub 是第三方中继
# （设计文档 §7.1），默认关闭；这里的端点只把网页上的增删改、启停、试拉、
# "立即跑一轮"翻译成 dc 的同名调用，**业务规则一律留在 dc**，网关不重复实现
# —— 否则就会出现"网页能删 dc 不让删"这类两套口径。


class RsshubSourceIn(BaseModel):
    name: str
    url: str
    enabled: bool = False
    credibility: str = "low"


class RsshubSourcePatch(BaseModel):
    name: str | None = None
    url: str | None = None
    enabled: bool | None = None


class RsshubTestIn(BaseModel):
    url: str
    limit: int = 10


RSSHUB_TIMEOUT_SEC = 60.0
# 一轮可能扫多个源（单源超时 15s + 入库），网关要给足，否则前端拿到 504 而 dc 还在跑
RSSHUB_RUN_TIMEOUT_SEC = 300.0


async def _proxy_rsshub(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    timeout: float = RSSHUB_TIMEOUT_SEC,
):
    """原样转发到 dc 的 ``/api/v1/intel/<path>``，并保留 dc 的错误码与提示

    dc 的 4xx **都有业务含义**（400 = rsshub:// 没配 base URL、409 = 该源已有
    文档不允许删），网关不能吞成 500，否则前端只能显示一句"操作失败"。
    """
    import httpx

    url = f"{settings.INTEL_CLEANER_BASE_URL.rstrip('/')}/api/v1/intel/{path.lstrip('/')}"
    headers = (
        {"X-API-Key": settings.INTEL_CLEANER_API_KEY}
        if settings.INTEL_CLEANER_API_KEY
        else {}
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as cli:
            resp = await cli.request(method, url, json=json_body, headers=headers)
    except httpx.HTTPError as e:
        return Response.fail(code=502, msg=f"无法连接 data-cleaner（{url}）：{e}")

    if resp.status_code >= 400:
        detail = ""
        try:
            detail = (resp.json() or {}).get("detail", "")
        except Exception:  # noqa: BLE001
            detail = resp.text[:200]
        return Response.fail(code=resp.status_code, msg=detail or "data-cleaner 拒绝")
    return Response.success(data=resp.json())


@router.get("/rsshub/status", summary="RSSHub 接入状态（转发 dc）")
async def rsshub_status(_user: User = Depends(get_current_user)):
    return await _proxy_rsshub("GET", "rsshub/status")


@router.get("/rsshub/sources", summary="RSSHub 源清单（转发 dc）")
async def rsshub_list_sources(_user: User = Depends(get_current_user)):
    return await _proxy_rsshub("GET", "rsshub/sources")


@router.post("/rsshub/sources", summary="登记 RSSHub 源（转发 dc）")
async def rsshub_add_source(
    body: RsshubSourceIn, _user: User = Depends(get_current_user)
):
    return await _proxy_rsshub("POST", "rsshub/sources", json_body=body.model_dump())


@router.patch("/rsshub/sources/{source_id}", summary="修改 RSSHub 源（转发 dc）")
async def rsshub_update_source(
    source_id: int,
    body: RsshubSourcePatch,
    _user: User = Depends(get_current_user),
):
    return await _proxy_rsshub(
        "PATCH", f"rsshub/sources/{source_id}", json_body=body.model_dump(exclude_none=True)
    )


@router.delete("/rsshub/sources/{source_id}", summary="删除 RSSHub 源（转发 dc）")
async def rsshub_delete_source(
    source_id: int, _user: User = Depends(get_current_user)
):
    return await _proxy_rsshub("DELETE", f"rsshub/sources/{source_id}")


@router.post("/rsshub/run", summary="立即跑一轮 RSSHub 摄取（转发 dc）")
async def rsshub_run(_user: User = Depends(get_current_user)):
    return await _proxy_rsshub("POST", "rsshub/run", timeout=RSSHUB_RUN_TIMEOUT_SEC)


@router.post("/rsshub/test", summary="试探 feed URL 是否可拉通（转发 dc，不入库）")
async def rsshub_test(body: RsshubTestIn, _user: User = Depends(get_current_user)):
    return await _proxy_rsshub("POST", "rsshub/test", json_body=body.model_dump())
