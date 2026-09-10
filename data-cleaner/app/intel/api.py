"""intel REST 路由（占位骨架）

本文件不含业务逻辑，仅定义开关门控下可被 frontend / backend 调用的占位端点。
实际实现在后续阶段（P0 RSS / P1 理解层 / P2 画像 / P3 因子化）逐步填充。

路由规划：
  - GET /intel/health   公开健康检查（监控用，始终挂载，由 main.py 的 /api/v1 前缀 + 本文件 prefix 合成）
  - GET /intel/status   受保护占位（需 X-API-Key），返回模块启用状态与待办清单

完整端点清单见设计文档 §9：sources / documents / feed_health / authors / factors / style。
"""
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from sqlalchemy import text

from app.core.security import verify_api_key
from app.intel.core.config import settings
from app.intel.core.logging import get_logger

logger = get_logger(__name__)

# 公开路由（无 X-API-Key）
public_router = APIRouter(tags=["intel-系统"])
# 受保护路由（需 X-API-Key，与 dc 其他内部端点一致）
router = APIRouter(tags=["intel"], dependencies=[Depends(verify_api_key)])

# ---- 上传限制（防止一次拖入整个磁盘 / 巨文件打爆内存）----
UPLOAD_ALLOWED_EXT = {".html", ".htm", ".mhtml", ".txt", ".md", ".csv", ".zip"}
# zip 解出来的文件不再二次解压（防递归炸弹），故内层白名单不含 .zip
INNER_ALLOWED_EXT = {".html", ".htm", ".mhtml", ".txt", ".md", ".csv"}
UPLOAD_MAX_FILES = 200
UPLOAD_MAX_BYTES = 20 * 1024 * 1024  # 单文件 20MB
# ---- zip 解压限制 ----
ZIP_MAX_ENTRIES = 500          # 一个 zip 最多取 500 个条目
ZIP_MAX_TOTAL_BYTES = 200 * 1024 * 1024   # 解压后总量上限 200MB
UPLOAD_TMP_TTL_HOURS = 24  # 上传临时目录的兜底存活时间（见 _sweep_stale_uploads）
ZIP_MAX_RATIO = 200
# 上传后立即抽取的单次上限：LLM 抽取实测 3~4s/篇（并发 4），一次抽太多会把
# 请求拖到网关超时。超出部分不丢，只是留到下次脚本/定时任务再抽，并在响应里
# 用 extraction.truncated 明示，绝不让用户以为"抽完了"。
UPLOAD_EXTRACT_LIMIT = 50            # 单文件压缩比上限，防 zip bomb


@public_router.get("/health")
async def intel_health() -> dict:
    """intel 模块健康检查（公开）

    注意：本端点始终可用（即使模块逻辑未实现），用于确认 dc 已挂载 /intel 路由。
    模块是否真正启用以 ``INTEL_ENABLED`` 为准。
    """
    return {
        "status": "healthy",
        "service": "data-cleaner",
        "module": "intel",
        "enabled": bool(settings.INTEL_ENABLED),
    }


@router.get("/status")
async def intel_status() -> dict:
    """intel 模块状态（占位，受保护）

    TODO(P2-P3): 补画像数、最近因子构建时间等。
    """
    logger.info("intel /status 被调用（占位）", extra={"module": "intel"})
    return {
        "module": "intel",
        "enabled": bool(settings.INTEL_ENABLED),
        "llm_provider": settings.INTEL_LLM_PROVIDER,
        "todo": [
            "P1 理解层 + LLM 抽取",
            "P2 作者/来源画像",
            "P3 INTL_* 因子化",
        ],
    }


@router.get("/feed-health")
async def intel_feed_health() -> dict:
    """每源最新健康状态（P0-7）：DISTINCT ON 取每源最近一次巡检记录"""
    from app.intel.core.db import current_session

    async with current_session() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT DISTINCT ON (h.source_id)
                           h.source_id, s.name, s.source_type, s.url,
                           h.checked_at, h.status, h.latency_ms, h.error, h.last_item_at
                    FROM intel.feed_health h
                    JOIN intel.sources s ON s.id = h.source_id
                    ORDER BY h.source_id, h.checked_at DESC
                    """
                )
            )
        ).mappings().all()
    items = [dict(r) for r in rows]
    return {
        "count": len(items),
        "ok": sum(1 for r in items if r["status"] == "ok"),
        "partial": sum(1 for r in items if r["status"] == "partial"),
        "failed": sum(1 for r in items if r["status"] == "failed"),
        "sources": items,
    }


@router.get("/sources")
async def intel_sources() -> dict:
    """源清单（含未启用），供前端管理页与调试"""
    from app.intel.core.db import current_session

    async with current_session() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT id, source_type, name, url, credibility,
                           poll_interval_sec, robots_ok, enabled, created_at
                    FROM intel.sources ORDER BY id
                    """
                )
            )
        ).mappings().all()
    items = [dict(r) for r in rows]
    return {"count": len(items), "sources": items}


# --------------------------------------------------------------------------
# P4-1 批量上传（人工投喂）
# --------------------------------------------------------------------------
def _safe_filename(name: str) -> str:
    """清洗上传文件名：去掉路径穿越段与非法字符，保留中英文/数字/常见符号"""
    base = PurePosixPath((name or "").replace("\\", "/")).name
    base = re.sub(r"[^\w.\-+()（）\[\]【】\u4e00-\u9fff]", "_", base)
    base = base.lstrip(".") or "unnamed"
    return base[:120]


def _decode_zip_name(info: zipfile.ZipInfo) -> str:
    """还原 zip 内的文件名（Windows 中文 zip 常见坑）

    zip 只在 flag_bits & 0x800 时标注 UTF-8；Windows 资源管理器/2345 好压等
   不加该标志，中文名按 cp437 存字节，直接读会是一串乱码（如 ``╞Θ┬╩.html``）。
    这里按 cp437→gbk 还原，失败再退 utf-8，都失败则原样返回。
    """
    raw = info.filename
    if info.flag_bits & 0x800:
        return raw
    for enc in ("gbk", "utf-8"):
        try:
            return raw.encode("cp437").decode(enc)
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
    return raw


def _extract_zip(zip_path: Path, dest: Path) -> tuple[list[tuple[Path, str]], list[dict]]:
    """安全解压，返回 ([(磁盘路径, 包内相对名)], [被跳过的条目])

    防护：① Zip Slip —— 逐条校验解压目标落在 dest 内；② zip bomb —— 压缩比与总量双限；
    ③ 噪音过滤 —— 目录、``__MACOSX/``、点开头文件；④ 只取文章扩展名，嵌套 zip 直接跳过。
    """
    kept: list[tuple[Path, str]] = []
    skipped: list[dict] = []
    total = 0
    dest_resolved = dest.resolve()

    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            rel = _decode_zip_name(info).replace("\\", "/")
            base = PurePosixPath(rel).name
            if rel.startswith("__MACOSX/") or base.startswith(".") or base == ".DS_Store":
                continue
            if len(kept) + len(skipped) >= ZIP_MAX_ENTRIES:
                skipped.append({"file": rel, "error": f"超出单 zip {ZIP_MAX_ENTRIES} 个条目上限"})
                break

            ext = Path(base).suffix.lower()
            if ext not in INNER_ALLOWED_EXT:
                skipped.append({
                    "file": rel,
                    "error": "嵌套 zip 不再解压" if ext == ".zip" else f"不支持的扩展名 {ext or '(无)'}",
                })
                continue
            if info.compress_size > 0 and info.file_size / info.compress_size > ZIP_MAX_RATIO:
                skipped.append({"file": rel, "error": "压缩比异常，疑为 zip bomb，已跳过"})
                continue
            total += info.file_size
            if total > ZIP_MAX_TOTAL_BYTES:
                skipped.append({"file": rel, "error": "解压总量超上限，已截断"})
                break

            target = (dest / rel)
            # Zip Slip：解析后必须仍在 dest 内
            if not str(target.resolve()).startswith(str(dest_resolved)):
                skipped.append({"file": rel, "error": "非法路径（Zip Slip），已跳过"})
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            kept.append((target, rel))

    return kept, skipped


def _rmtree_retry(path: Path, attempts: int = 5, delay: float = 0.3) -> bool:
    """删除上传临时目录（带退避重试）

    Windows 上刚落盘的文件常被杀软/索引服务短暂占用，`shutil.rmtree` 会抛
    PermissionError；用 ``ignore_errors=True`` 会**静默吞掉**并留下垃圾目录
    （实测跑几轮就堆了十几个）。这里退避重试若干次，最终仍失败只记 warning
    —— 原文已按 content_hash 独立落盘（raw_path），残留临时文件不构成数据丢失。
    """
    for i in range(attempts):
        try:
            shutil.rmtree(path)
            return True
        except OSError as e:  # noqa: PERF203
            if i == attempts - 1:
                logger.warning(f"P4-1 上传临时目录清理失败（可手工删除）{path}: {e}")
                return False
            time.sleep(delay * (i + 1))
    return False




def _ingest_file(
    path: Path, source_name: str, source_type: str, dry_run: bool, display: str | None = None
) -> dict:
    """单文件入库（同步，跑在线程池）。失败由调用方捕获，不影响同批其他文件"""
    label = display or path.name
    if dry_run:
        from app.intel.ingest.manual import ManualSource

        items = ManualSource(source_name).collect(str(path))
        return {
            "file": label, "status": "dry_run", "parsed": len(items),
            "titles": [it.title[:60] for it in items[:5]],
        }

    from app.intel.service import run_manual_ingest, run_wechat_ingest

    fn = run_wechat_ingest if source_type == "wechat" else run_manual_ingest
    st = fn(str(path), source_name=source_name)
    return {
        "file": label, "status": "ok",
        "fetched": st.get("fetched", 0), "new": st.get("new", 0),
        "dup": st.get("dup", 0), "reposts": st.get("reposts", 0),
        "source": st.get("source"), "source_id": st.get("source_id"),
        # 本次真正入库的文档 id：上传后立即抽取要精确限定在这一批，
        # 不能用 id 区间猜，也不能按 source 反查（源可能是复用的老源）
        "doc_ids": list(st.get("doc_ids") or []),
    }


def _sweep_stale_uploads(root: Path, ttl_hours: int = UPLOAD_TMP_TTL_HOURS) -> int:
    """清掉超期未消失的上传临时目录（自愈）

    Windows 上实时防护（Defender 等）会在扫描期间以 FILE_SHARE_DELETE 持有刚落盘
    的文件句柄：此时 `rmtree` **返回成功**、Python 侧 `exists()` 已是 False，但磁盘上
    目录项仍可枚举，要等扫描结束才真正消失（实测滞后一轮）。这既不是代码漏删也
    不是泄漏，但 `_uploads` 会显得越堆越多。下次上传顺手扫一遍超期目录即可自愈。
    """
    if not root.exists():
        return 0
    cutoff = time.time() - ttl_hours * 3600
    swept = 0
    for p in list(root.iterdir()):
        try:
            if p.is_dir() and p.stat().st_mtime < cutoff and _rmtree_retry(p, attempts=2, delay=0.1):
                swept += 1
        except OSError:  # noqa: PERF203 —— 目录可能在枚举期间已消失，跳过即可
            continue
    if swept:
        logger.info(f"P4-1 清扫超期上传临时目录 {swept} 个（>{ttl_hours}h）")
    return swept


def _extract_after_upload(doc_ids: list[int], limit: int) -> dict:
    """上传后立即对**这一批**文档跑理解层抽取（预筛 → LLM → doc_mentions/doc_style）

    纪律：
    - 只抽 doc_ids，不碰历史欠账（历史欠账归每日脚本/定时任务）。
    - LLM 未配置或不可达**不影响上传结果**（文档已入库），只在 extraction 里
      说明原因——上传成功就是成功，不能因为下游抖动让整批回滚或报错。
    - 超过 limit 的部分留待后续，用 truncated 明示，不假装抽完。
    """
    if not doc_ids:
        return {"status": "skipped", "reason": "本次没有新增文档（都是重复/转载）",
                "requested": 0, "done": 0, "truncated": False}
    if not (settings.INTEL_LLM_BASE_URL and settings.INTEL_LLM_API_KEY
            and settings.INTEL_LLM_MODEL):
        return {"status": "skipped", "reason": "INTEL_LLM_* 未配置，跳过抽取",
                "requested": len(doc_ids), "done": 0, "truncated": False}

    todo = doc_ids[:limit]
    try:
        from app.intel.understand import service as usvc

        s = usvc.run_understanding_batch(limit=len(todo), doc_ids=todo)
    except Exception as e:  # noqa: BLE001 —— 下游抖动不影响"上传成功"
        logger.warning(f"P4-1 上传后立即抽取失败: {type(e).__name__}: {e}")
        return {"status": "error", "error": f"{type(e).__name__}: {str(e)[:200]}",
                "requested": len(doc_ids), "done": 0, "truncated": len(doc_ids) > len(todo)}

    out = {"status": "ok", "requested": len(doc_ids), "done": len(todo),
           "truncated": len(doc_ids) > len(todo)}
    for k in ("candidates", "prescreen_hit", "prescreen_miss", "extracted",
              "no_mention", "quarantined", "api_fail", "cost_cny", "stopped_reason"):
        out[k] = s.get(k)
    if s.get("stopped_reason") == "budget":
        out["status"] = "budget_stopped"
    return out


@router.post("/upload", summary="批量上传文章文件并入库（P4-1 人工投喂）")
async def intel_upload(
    files: list[UploadFile] = File(
        ..., description="文章文件：html/htm/mhtml/txt/md/csv，或打包的 zip（自动解压）"
    ),
    source_name: str = Form("", description="源名（如公众号名）；留空为『上传』"),
    source_type: str = Form("manual", description="manual | wechat —— 解析相同，仅影响溯源标记"),
    dry_run: bool = Form(False, description="只解析不写库，用于先看解析质量"),
    extract: bool = Form(False, description="入库后立即对本次新增文档跑理解层抽取（花钱）"),
    extract_limit: int = Form(
        UPLOAD_EXTRACT_LIMIT,
        description=f"立即抽取的篇数上限（默认 {UPLOAD_EXTRACT_LIMIT}，超出留待后续）",
    ),
) -> dict:
    """批量上传文章文件 → 落盘 → 复用 P4-1/P4-2 既有链路入库

    zip 会在服务端自动解压（中文名按 cp437→gbk 还原；防 Zip Slip / zip bomb；
    嵌套 zip 不再二次解压）。展示名形如 ``包名.zip!子目录/文章.html`` 便于溯源。

    纪律：单文件失败**隔离**（其余继续）；原文还需经理解层抽取才算进入全链路：
    ``python scripts/run_intel_understanding.py --apply``
    """
    import anyio

    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="未选择文件")
    if len(files) > UPLOAD_MAX_FILES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"一次最多 {UPLOAD_MAX_FILES} 个文件，当前 {len(files)} 个",
        )
    if source_type not in {"manual", "wechat"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source_type 只能是 manual 或 wechat",
        )

    name = (source_name or "").strip() or "上传"
    # 临时目录**刻意不放在 INTEL_INBOX_DIR 下**：见 config.INTEL_UPLOAD_TMP_DIR 注释
    inbox = Path(settings.INTEL_UPLOAD_TMP_DIR).expanduser()
    _sweep_stale_uploads(inbox)  # 自愈：清掉超期未真正消失的旧临时目录
    # 目录名必须唯一：只用毫秒时间戳的话，同一毫秒内的两个上传会**共用同一个
    # 临时目录** —— 后完成的请求 rmtree 会把先一个请求正在读的文件删掉（并发
    # 上传时文件凭空消失）。加 uuid 短后缀彻底隔开。
    #
    # 目录**必须只有一层**（不能 _uploads/<源名>/<ts>）：删完 ts 再删空的源名层
    # 会踩 Windows delete-on-close 竞态 —— 下次上传立刻重建同名目录，此后 rmtree
    # 全部静默失败（实测第二轮起每个上传都留一个垃圾目录）。扁平命名下每次只删
    # 一个独立目录，不留空壳也没有重建竞态。
    tmp_dir = inbox / (
        f"{_safe_filename(name)}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    )
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # (磁盘路径, 展示名) —— zip 内的文件展示为 "包名!包内相对路径"
    saved: list[tuple[Path, str]] = []
    rejected: list[dict] = []
    extracted = 0  # 从 zip 解出的文章文件数
    for f in files:
        raw = _safe_filename(f.filename)
        ext = Path(raw).suffix.lower()
        if ext not in UPLOAD_ALLOWED_EXT:
            rejected.append({"file": raw, "error": f"不支持的扩展名 {ext or '(无)'}"})
            continue
        data = await f.read()
        if len(data) > UPLOAD_MAX_BYTES:
            rejected.append({
                "file": raw,
                "error": f"超过单文件上限 {UPLOAD_MAX_BYTES // 1024 // 1024}MB",
            })
            continue
        dest = tmp_dir / raw
        i = 1
        while dest.exists():  # 同名不覆盖，加序号
            dest = tmp_dir / f"{Path(raw).stem}_{i}{ext}"
            i += 1
        dest.write_bytes(data)

        if ext != ".zip":
            saved.append((dest, raw))
            continue

        # zip：解压后逐个当文章文件处理（不再二次解压）
        try:
            kept, skipped = _extract_zip(dest, tmp_dir / f"_unzipped_{dest.stem}")
        except Exception as e:  # noqa: BLE001 —— 坏包隔离，不影响同批其他文件
            logger.warning(f"P4-1 zip 解压失败 {raw}: {type(e).__name__}: {e}")
            rejected.append({"file": raw, "error": f"解压失败：{type(e).__name__}"})
            continue
        for p, rel in kept:
            saved.append((p, f"{raw}!{rel}"))
        extracted += len(kept)
        for s in skipped:
            rejected.append({"file": f"{raw}!{s['file']}", "error": s["error"]})

    if not saved:
        _rmtree_retry(tmp_dir)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="没有可导入的文件：" + "；".join(r["error"] for r in rejected[:3]),
        )

    results: list[dict] = []
    for p, label in saved:
        try:
            results.append(
                await anyio.to_thread.run_sync(
                    _ingest_file, p, name, source_type, dry_run, label
                )
            )
        except Exception as e:  # noqa: BLE001 —— 单文件隔离，不中断整批
            logger.warning(f"P4-1 上传入库失败 {label}: {type(e).__name__}: {e}")
            results.append({
                "file": label, "status": "error",
                "error": f"{type(e).__name__}: {str(e)[:200]}",
            })

    # 原文已按 content_hash 独立落盘（raw_path），上传临时目录可安全清理
    _rmtree_retry(tmp_dir)

    ok_rows = [r for r in results if r["status"] == "ok"]
    # failed 只数「真失败」：status=error 的 + 被拒收的。
    # 不能写成 len(results) - len(ok_rows)，否则 dry_run 的每个文件都会被算成失败
    # （实测 dry-run 3 篇 + 1 个嵌套 zip → failed=4，前端误报"3 个文件失败"）。
    err_rows = [r for r in results if r["status"] == "error"]

    # 上传后自动抽取（默认关）：只抽本次真正新增的文档
    new_ids: list[int] = []
    for r in ok_rows:
        new_ids.extend(r.get("doc_ids") or [])
    extraction = None
    if extract and not dry_run:
        extraction = await anyio.to_thread.run_sync(
            _extract_after_upload, new_ids, extract_limit
        )
        if extraction:
            logger.info(
                f"P4-1 上传后抽取 status={extraction['status']} "
                f"done={extraction.get('done')}/{extraction.get('requested')} "
                f"extracted={extraction.get('extracted')}",
                extra={"task": "intel_upload"},
            )

    next_step = (
        "_p2_build_profiles.py + _p3_build_factors.py（画像 + 因子，零 LLM 成本）"
        if extraction and extraction["status"] in {"ok", "budget_stopped"}
        else "scripts/run_intel_understanding.py --apply（抽取→画像→因子）"
    )
    return {
        "source": name,
        "source_type": source_type,
        "dry_run": dry_run,
        "uploaded": len(files),
        "accepted": len(saved),
        "extracted": extracted,  # 从 zip 解出的文件数（便于核对"传 1 个包进来 25 篇"）
        "failed": len(err_rows) + len(rejected),
        "fetched": sum(r.get("fetched", 0) for r in ok_rows),
        "new": sum(r.get("new", 0) for r in ok_rows),
        "dup": sum(r.get("dup", 0) for r in ok_rows),
        "reposts": sum(r.get("reposts", 0) for r in ok_rows),
        "rejected": rejected,
        "files": results,
        "extraction": extraction,  # 仅 extract=true 时非空
        "next": next_step,
    }
