"""资讯分析 intel 数据接口

读 Postgres ``intel`` schema（由 data-cleaner 产出），暴露两个只读端点：
- GET /api/v1/intel/mentions  资讯抽取结果（doc_mentions JOIN documents + sources）
- GET /api/v1/intel/profiles  作者/来源画像（author_profiles）

字段一律以 camelCase 返回，前端 news-analysis 组件可直接消费，无需转换。
"""
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
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


@router.get("/mentions", summary="资讯抽取结果")
async def list_mentions(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    sql = text(
        """
        SELECT m.id,
               m.doc_id,
               d.title,
               m.symbol,
               m.stance,
               m.confidence,
               m.horizon,
               m.thesis,
               m.evidence,
               d.author,
               s.name AS source_name,
               d.published_at
        FROM intel.doc_mentions m
        JOIN intel.documents d ON d.id = m.doc_id
        LEFT JOIN intel.sources s ON s.id = d.source_id
        WHERE m.prompt_version = :pv
        ORDER BY d.published_at DESC NULLS LAST, m.id DESC
        """
    )
    res = await db.execute(sql, {"pv": PROMPT_VERSION})
    rows = res.mappings().all()
    data = [
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
    return Response.success(data=data)


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
