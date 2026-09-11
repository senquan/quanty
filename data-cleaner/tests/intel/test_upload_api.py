"""P4-1 批量上传接口测试（POST /api/v1/intel/upload）

说明：直接把 intel router 挂到测试用 FastAPI 上，不依赖 INTEL_ENABLED 门控
（dc 未配置 API_KEYS，verify_api_key 自动放行）。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.intel import store
from app.intel.api import router as intel_router
from app.intel.api import _safe_filename
from app.intel.store import get_engine

app = FastAPI()
app.include_router(intel_router, prefix="/api/v1/intel")
client = TestClient(app)

SRC = "pytest_upload"
DEFAULT_SRC = "上传"  # 不传 source_name 时的兜底源名

HTML = """<html><head><title>茅台动销回暖</title>
<meta name="author" content="pytest 作者"></head>
<body><article><h1>茅台动销回暖</h1>
<p>贵州茅台（600519.SH）三季度动销回暖，渠道库存下降。</p>
<p>我们认为龙头具备配置价值。</p></article></body></html>"""

PLAIN = "五粮液（000858.SZ）批价企稳，动销改善。\n渠道反馈库存处于低位。"


def _cleanup(*names: str) -> None:
    """删除测试源及其全部附属数据

    为什么必须连 documents 一起删：``store.existing_document_keys`` 里
    ``content_hash`` 去重是**跨源全局**的（同源文章被别的源转载只留首发），
    所以任何残留文档都会让后续用例（乃至**下一次运行**）的相同内容被判重复，
    表现为"莫名其妙 new=0"的偶发失败。清理必须完整，不能只删 sources。
    """
    with get_engine().begin() as c:
        for n in names:
            c.execute(store.text(
                "DELETE FROM intel.doc_mentions WHERE doc_id IN "
                "(SELECT id FROM intel.documents WHERE source_id IN "
                " (SELECT id FROM intel.sources WHERE name = :n))"), {"n": n})
            c.execute(store.text(
                "DELETE FROM intel.documents WHERE source_id IN "
                "(SELECT id FROM intel.sources WHERE name = :n)"), {"n": n})
            c.execute(store.text(
                "DELETE FROM intel.feed_health WHERE source_id IN "
                "(SELECT id FROM intel.sources WHERE name = :n)"), {"n": n})
            c.execute(store.text("DELETE FROM intel.sources WHERE name = :n"), {"n": n})


@pytest.fixture(autouse=True)
def _clean():
    _cleanup(SRC, DEFAULT_SRC)
    yield
    _cleanup(SRC, DEFAULT_SRC)


def _post(files, **data):
    return client.post("/api/v1/intel/upload", files=files, data=data)


def test_upload_html_ingests():
    r = _post([("files", ("a.html", HTML, "text/html"))], source_name=SRC)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["source"] == SRC
    assert b["accepted"] == 1
    assert b["new"] == 1 and b["dup"] == 0, b
    assert b["failed"] == 0
    assert b["files"][0]["status"] == "ok"


def test_upload_plain_text_ingests():
    r = _post([("files", ("b.txt", PLAIN, "text/plain"))], source_name=SRC)
    assert r.status_code == 200, r.text
    assert r.json()["new"] == 1


def test_upload_batch_multiple_files():
    r = _post(
        [
            ("files", ("a.html", HTML, "text/html")),
            ("files", ("b.txt", PLAIN, "text/plain")),
            ("files", ("c.md", "# 标题\n内容", "text/markdown")),
        ],
        source_name=SRC,
    )
    b = r.json()
    assert b["uploaded"] == 3 and b["accepted"] == 3
    assert b["new"] == 3


def test_upload_dedupe_second_time():
    _post([("files", ("a.html", HTML, "text/html"))], source_name=SRC)
    b = _post([("files", ("a.html", HTML, "text/html"))], source_name=SRC).json()
    assert b["new"] == 0 and b["dup"] == 1  # content_hash 幂等


def test_upload_rejects_unsupported_ext():
    r = _post([("files", ("x.pdf", "junk", "application/pdf"))], source_name=SRC)
    assert r.status_code == 400  # 全部被拒 → 400，并说明原因
    assert "不支持的扩展名" in r.json()["detail"]


def test_upload_mixed_partial_reject():
    r = _post(
        [
            ("files", ("a.html", HTML, "text/html")),
            ("files", ("x.exe", "junk", "application/octet-stream")),
        ],
        source_name=SRC,
    )
    b = r.json()
    assert b["accepted"] == 1 and b["new"] == 1
    assert b["rejected"] and b["rejected"][0]["file"] == "x.exe"


def test_upload_dry_run_does_not_write():
    r = _post([("files", ("a.html", HTML, "text/html"))],
              source_name=SRC, dry_run="true")
    b = r.json()
    assert b["dry_run"] is True
    assert b["files"][0]["status"] == "dry_run"
    assert b["files"][0]["parsed"] >= 1
    assert b["new"] == 0
    with get_engine().connect() as c:
        n = c.execute(store.text(
            "SELECT count(*) FROM intel.documents WHERE source_id IN "
            "(SELECT id FROM intel.sources WHERE name = :n)"), {"n": SRC}).scalar()
    assert n == 0  # 未写库


def test_upload_dry_run_is_not_counted_as_failure():
    """dry_run 的"试解析成功"不能算进 failed

    曾经写成 ``failed = len(results) - len(ok_rows) + len(rejected)``，
    dry_run 的每条结果 status 都是 ``dry_run`` 而非 ``ok``，于是被全数计为失败
    —— 实测 3 篇试解析 + 1 个嵌套 zip → failed=4，前端误报"3 个文件失败"。
    """
    b = _post([("files", ("a.html", HTML, "text/html"))],
              source_name=SRC, dry_run="true").json()
    assert b["files"][0]["status"] == "dry_run"
    assert b["failed"] == 0, b  # 没有拒收项就不该有失败
    assert b["new"] == 0


def test_upload_rejects_bad_source_type():
    r = _post([("files", ("a.html", HTML, "text/html"))],
              source_name=SRC, source_type="rss")
    assert r.status_code == 400


def test_upload_wechat_source_type():
    r = _post([("files", ("a.html", HTML, "text/html"))],
              source_name=SRC, source_type="wechat")
    b = r.json()
    assert b["source_type"] == "wechat" and b["new"] == 1
    with get_engine().connect() as c:
        t = c.execute(store.text(
            "SELECT source_type FROM intel.sources WHERE name = :n"), {"n": SRC}).scalar()
    assert t == "wechat"


def test_upload_default_source_name():
    r = _post([("files", ("a.html", HTML, "text/html"))])
    assert r.status_code == 200
    assert r.json()["source"] == DEFAULT_SRC
    _cleanup(DEFAULT_SRC)  # 兜底源名不归 autouse 夹具管，用完即清


def test_safe_filename_blocks_traversal():
    assert _safe_filename("../../etc/passwd") == "passwd"
    assert _safe_filename("..\\..\\win.ini") == "win.ini"
    assert _safe_filename("正常 文件(1).html") == "正常_文件(1).html"
    assert _safe_filename("") == "unnamed"
    assert _safe_filename("...") == "unnamed"


# --------------------------------------------------------------------------
# zip 自动解压
# --------------------------------------------------------------------------
def _make_zip(entries: dict) -> bytes:
    """构造 zip 字节流。entries: 包内相对路径 -> 内容（str/bytes）"""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, content in entries.items():
            zf.writestr(rel, content)
    return buf.getvalue()


def _make_zip_windows_cn(name: str, content: str) -> bytes:
    """手工造一个真正的「Windows 中文包」：名按 gbk 字节存、且未置 UTF-8 标志 0x800

    ``zipfile`` 的 ``ZipInfo._encodeFilenameFlags`` 只要文件名非 ASCII 就强制
    UTF-8 编码并置 0x800，用它**造不出** Windows 资源管理器那种包（实测即使
    手动清标志也会被加回去）。所以这里先写等长 ASCII 占位名，再原位替换成
    gbk 字节 —— 长度不变，因此偏移量/CRC 全都不用改。
    """
    import io
    import zipfile

    raw = name.encode("gbk")
    placeholder = b"P" * len(raw)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(placeholder.decode("ascii"), content)
    data = buf.getvalue()
    # 名字在「本地文件头」和「中央目录」各出现一次
    assert data.count(placeholder) == 2, data.count(placeholder)
    return data.replace(placeholder, raw)


def _post_zip(payload: bytes, filename="bundle.zip", **data):
    return _post([("files", (filename, payload, "application/zip"))], **data)


def test_upload_zip_extracts_and_ingests():
    payload = _make_zip({
        "a.html": HTML,
        "sub/b.txt": PLAIN,
    })
    b = _post_zip(payload, source_name=SRC).json()
    assert b["uploaded"] == 1
    assert b["extracted"] == 2 and b["accepted"] == 2
    assert b["new"] == 2
    # 展示名带包名与包内相对路径，便于溯源
    names = sorted(f["file"] for f in b["files"])
    assert names == ["bundle.zip!a.html", "bundle.zip!sub/b.txt"]


def test_upload_zip_skips_noise_and_nested_zip():
    inner = _make_zip({"inner.html": HTML})
    payload = _make_zip({
        "ok.html": HTML,
        "nested.zip": inner,
        "__MACOSX/._ok.html": "junk",
        ".DS_Store": "junk",
        "readme.pdf": "junk",
    })
    b = _post_zip(payload, source_name=SRC).json()
    assert b["accepted"] == 1 and b["new"] == 1  # 只有 ok.html 入库
    errs = " ".join(r["error"] for r in b["rejected"])
    assert "嵌套 zip 不再解压" in errs
    assert ".pdf" in errs


def test_upload_zip_windows_chinese_name():
    """模拟 Windows 打的不带 UTF-8 标志的中文 zip：名按 cp437 存，需还原成 gbk"""
    payload = _make_zip_windows_cn("中文文章.html", HTML)
    b = _post_zip(payload, source_name=SRC).json()
    assert b["accepted"] == 1 and b["new"] == 1
    assert b["files"][0]["file"].endswith("中文文章.html"), b["files"]


def test_upload_zip_blocks_zip_slip(tmp_path):
    """恶意 zip 企图写到解压目录之外 → 跳过，且不落盘到外面"""
    payload = _make_zip({
        "../escape.html": HTML,
        "safe.html": HTML,
    })
    b = _post_zip(payload, source_name=SRC).json()
    assert b["accepted"] == 1 and b["new"] == 1
    errs = " ".join(r["error"] for r in b["rejected"])
    assert "Zip Slip" in errs
    # 确认没写到 tmp 根目录外
    assert not list(tmp_path.glob("escape.html"))


def test_upload_zip_no_usable_file_returns_400():
    payload = _make_zip({"only.pdf": "junk"})
    r = _post_zip(payload, source_name=SRC)
    assert r.status_code == 400
    assert "不支持的扩展名" in r.json()["detail"]


def test_upload_zip_dry_run():
    payload = _make_zip({"a.html": HTML})
    b = _post_zip(payload, source_name=SRC, dry_run="true").json()
    assert b["dry_run"] is True and b["extracted"] == 1
    assert b["files"][0]["status"] == "dry_run"
    assert b["new"] == 0


def test_upload_corrupt_zip_isolated():
    """坏 zip 只让自己失败，同批其他文件照常入库"""
    r = _post(
        [
            ("files", ("a.html", HTML, "text/html")),
            ("files", ("bad.zip", b"not-a-zip", "application/zip")),
        ],
        source_name=SRC,
    )
    b = r.json()
    assert b["new"] == 1
    assert any("解压失败" in x["error"] for x in b["rejected"])


def _tmp_dirs(root: Path) -> set:
    """_uploads 下的目录集合（用本次差集判定，历史垃圾不影响断言）"""
    return {p for p in root.rglob("*") if p.is_dir()} if root.exists() else set()


def test_upload_cleans_up_temp_dir():
    """上传临时目录用完即删：普通文件与 zip 解压目录都不能留残骸"""
    from pathlib import Path

    from app.intel.core.config import settings

    base = Path(settings.INTEL_UPLOAD_TMP_DIR).expanduser()
    before = _tmp_dirs(base)

    _post([("files", ("a.html", HTML, "text/html"))], source_name=SRC)
    _post_zip(_make_zip({"a.html": HTML, "sub/b.txt": PLAIN}), source_name=SRC)

    after = _tmp_dirs(base)
    assert after - before == set(), sorted(str(p) for p in (after - before))


def test_upload_tmp_dir_name_is_unique(monkeypatch):
    """两次上传必须用不同的临时目录

    目录名若只取毫秒时间戳，同一毫秒内并发的两个上传会共用同一目录：
    后完成的那个 `rmtree` 会把前一个请求正在读的文件删掉。
    """
    import app.intel.api as api

    seen: list[str] = []
    real = api._rmtree_retry

    def spy(tmp_dir):
        seen.append(str(tmp_dir))
        return real(tmp_dir)

    monkeypatch.setattr(api, "_rmtree_retry", spy)
    _post([("files", ("a.html", HTML, "text/html"))], source_name=SRC)
    _post([("files", ("b.txt", PLAIN, "text/plain"))], source_name=SRC)

    assert len(seen) == 2, seen
    assert seen[0] != seen[1], f"两次上传复用了同一临时目录：{seen[0]}"


def test_upload_sweeps_stale_temp_dirs():
    """超期临时目录在下次上传时被自动清扫

    Windows 上 rmtree 会「报成功」但目录项要等实时防护扫完才真正消失，
    于是 _uploads 会滞后地堆着一批。清扫逻辑保证它们不会长期留着。
    """
    import os
    import shutil
    import time
    from pathlib import Path

    from app.intel.core.config import settings

    base = Path(settings.INTEL_UPLOAD_TMP_DIR).expanduser()
    base.mkdir(parents=True, exist_ok=True)

    stale = base / "stale-dir"
    stale.mkdir(exist_ok=True)
    (stale / "x.html").write_text("old", encoding="utf-8")
    old = time.time() - 48 * 3600
    os.utime(stale, (old, old))

    fresh = base / "fresh-dir"
    fresh.mkdir(exist_ok=True)

    try:
        _post([("files", ("a.html", HTML, "text/html"))], source_name=SRC)
        assert not stale.exists(), "超期临时目录应被清扫"
        assert fresh.exists(), "未超期的目录不应被动"
    finally:
        shutil.rmtree(fresh, ignore_errors=True)
        shutil.rmtree(stale, ignore_errors=True)


def test_upload_sweeps_stale_temp_dirs():
    """超期临时目录在下次上传时被自动清扫

    Windows 上 rmtree 会「报成功」但目录项要等实时防护扫完才真正消失，
    于是 _uploads 会滞后地堆着一批。清扫逻辑保证它们不会长期留着。
    """
    import os
    import shutil
    import time
    from pathlib import Path

    from app.intel.core.config import settings

    base = Path(settings.INTEL_UPLOAD_TMP_DIR).expanduser()
    base.mkdir(parents=True, exist_ok=True)

    stale = base / "stale-dir"
    stale.mkdir(exist_ok=True)
    (stale / "x.html").write_text("old", encoding="utf-8")
    old = time.time() - 48 * 3600
    os.utime(stale, (old, old))

    fresh = base / "fresh-dir"
    fresh.mkdir(exist_ok=True)

    try:
        _post([("files", ("a.html", HTML, "text/html"))], source_name=SRC)
        assert not stale.exists(), "超期临时目录应被清扫"
        assert fresh.exists(), "未超期的目录不应被动"
    finally:
        shutil.rmtree(fresh, ignore_errors=True)
        shutil.rmtree(stale, ignore_errors=True)


# --------------------------------------------------------------------------
# 上传后立即抽取（extract=true）
# --------------------------------------------------------------------------

@pytest.fixture
def _fake_extract(monkeypatch):
    """拦下真实 LLM 调用：记录入参 + 返回可断言的假结果（测试不许花钱）"""
    calls: list[dict] = []

    def _run(limit: int = 100, doc_ids: list | None = None, **_kw):
        calls.append({"limit": limit, "doc_ids": list(doc_ids or [])})
        return {"candidates": len(doc_ids or []), "prescreen_hit": 1, "prescreen_miss": 0,
                "extracted": 1, "quarantined": 0, "no_mention": 0, "api_fail": 0,
                "cost_cny": 0.018, "stopped_reason": None}

    monkeypatch.setattr(
        "app.intel.understand.service.run_understanding_batch", _run, raising=True
    )
    return calls


def test_upload_extract_off_by_default(_fake_extract):
    """不传 extract 就一分钱都不花"""
    b = _post([("files", ("a.html", HTML, "text/html"))], source_name=SRC).json()
    assert b["extraction"] is None
    assert _fake_extract == []


def test_upload_extract_targets_only_new_docs(_fake_extract):
    """抽取范围必须精确等于本次新增文档，不能顺手把历史欠账一起抽了"""
    b = _post([("files", ("a.html", HTML, "text/html"))],
              source_name=SRC, extract=True).json()
    assert b["new"] == 1, b
    ex = b["extraction"]
    assert ex["status"] == "ok"
    assert ex["requested"] == 1 and ex["done"] == 1 and ex["truncated"] is False
    assert ex["extracted"] == 1 and ex["cost_cny"] > 0
    assert _fake_extract[0]["doc_ids"] == b["files"][0]["doc_ids"]


def test_upload_extract_skipped_when_no_new_docs(_fake_extract):
    """全是重复/转载时不该白跑一轮 LLM"""
    _post([("files", ("a.html", HTML, "text/html"))], source_name=SRC)
    _fake_extract.clear()
    b = _post([("files", ("a.html", HTML, "text/html"))],
              source_name=SRC, extract=True).json()
    assert b["new"] == 0 and b["dup"] == 1, b
    assert b["extraction"]["status"] == "skipped"
    assert _fake_extract == []


def test_upload_extract_respects_limit(_fake_extract):
    """超上限的部分留待后续，但必须明示 truncated，不能假装抽完"""
    files = [("files", (f"f{i}.txt",
                        f"第{i}篇：五粮液（000858.SZ）批价企稳，动销改善。渠道库存低位。",
                        "text/plain")) for i in range(3)]
    b = _post(files, source_name=SRC, extract=True, extract_limit=1).json()
    assert b["new"] == 3, b
    ex = b["extraction"]
    assert ex["requested"] == 3 and ex["done"] == 1 and ex["truncated"] is True
    assert len(_fake_extract[0]["doc_ids"]) == 1


def test_upload_extract_error_does_not_fail_upload(monkeypatch):
    """LLM 挂了不影响上传结果——文档已入库，抽取失败只在 extraction 里说明"""

    def _boom(**_kw):
        raise RuntimeError("LLM 不可达")

    monkeypatch.setattr(
        "app.intel.understand.service.run_understanding_batch", _boom, raising=True
    )
    r = _post([("files", ("a.html", HTML, "text/html"))], source_name=SRC, extract=True)
    assert r.status_code == 200, r.text  # 上传成功就是成功
    b = r.json()
    assert b["new"] == 1
    assert b["extraction"]["status"] == "error"
    assert "LLM 不可达" in b["extraction"]["error"]


def test_upload_extract_skipped_when_llm_unconfigured(monkeypatch, _fake_extract):
    from app.intel.api import settings as api_settings

    monkeypatch.setattr(api_settings, "INTEL_LLM_BASE_URL", "", raising=True)
    b = _post([("files", ("a.html", HTML, "text/html"))],
              source_name=SRC, extract=True).json()
    assert b["extraction"]["status"] == "skipped"
    assert _fake_extract == []


def test_upload_extract_ignored_in_dry_run(_fake_extract):
    """dry-run 没入库，无从抽取"""
    b = _post([("files", ("a.html", HTML, "text/html"))],
              source_name=SRC, extract=True, dry_run=True).json()
    assert b["extraction"] is None
    assert _fake_extract == []


# --------------------------------------------------------------------------
# 上传后立即抽取（extract=true）
# --------------------------------------------------------------------------

@pytest.fixture
def _fake_extract(monkeypatch):
    """拦下真实 LLM 调用：记录入参 + 返回可断言的假结果（测试不许花钱）"""
    calls: list[dict] = []

    def _run(limit: int = 100, doc_ids: list | None = None, **_kw):
        calls.append({"limit": limit, "doc_ids": list(doc_ids or [])})
        return {"candidates": len(doc_ids or []), "prescreen_hit": 1, "prescreen_miss": 0,
                "extracted": 1, "quarantined": 0, "no_mention": 0, "api_fail": 0,
                "cost_cny": 0.018, "stopped_reason": None}

    monkeypatch.setattr(
        "app.intel.understand.service.run_understanding_batch", _run, raising=True
    )
    return calls


def test_upload_extract_off_by_default(_fake_extract):
    """不传 extract 就一分钱都不花"""
    b = _post([("files", ("a.html", HTML, "text/html"))], source_name=SRC).json()
    assert b["extraction"] is None
    assert _fake_extract == []


def test_upload_extract_targets_only_new_docs(_fake_extract):
    """抽取范围必须精确等于本次新增文档，不能顺手把历史欠账一起抽了"""
    b = _post([("files", ("a.html", HTML, "text/html"))],
              source_name=SRC, extract=True).json()
    assert b["new"] == 1, b
    ex = b["extraction"]
    assert ex["status"] == "ok"
    assert ex["requested"] == 1 and ex["done"] == 1 and ex["truncated"] is False
    assert ex["extracted"] == 1 and ex["cost_cny"] > 0
    assert _fake_extract[0]["doc_ids"] == b["files"][0]["doc_ids"]


def test_upload_extract_skipped_when_no_new_docs(_fake_extract):
    """全是重复/转载时不该白跑一轮 LLM"""
    _post([("files", ("a.html", HTML, "text/html"))], source_name=SRC)
    _fake_extract.clear()
    b = _post([("files", ("a.html", HTML, "text/html"))],
              source_name=SRC, extract=True).json()
    assert b["new"] == 0 and b["dup"] == 1, b
    assert b["extraction"]["status"] == "skipped"
    assert _fake_extract == []


def test_upload_extract_respects_limit(_fake_extract):
    """超上限的部分留待后续，但必须明示 truncated，不能假装抽完"""
    files = [("files", (f"f{i}.txt",
                        f"第{i}篇：五粮液（000858.SZ）批价企稳，动销改善。渠道库存低位。",
                        "text/plain")) for i in range(3)]
    b = _post(files, source_name=SRC, extract=True, extract_limit=1).json()
    assert b["new"] == 3, b
    ex = b["extraction"]
    assert ex["requested"] == 3 and ex["done"] == 1 and ex["truncated"] is True
    assert len(_fake_extract[0]["doc_ids"]) == 1


def test_upload_extract_error_does_not_fail_upload(monkeypatch):
    """LLM 挂了不影响上传结果——文档已入库，抽取失败只在 extraction 里说明"""

    def _boom(**_kw):
        raise RuntimeError("LLM 不可达")

    monkeypatch.setattr(
        "app.intel.understand.service.run_understanding_batch", _boom, raising=True
    )
    r = _post([("files", ("a.html", HTML, "text/html"))], source_name=SRC, extract=True)
    assert r.status_code == 200, r.text  # 上传成功就是成功
    b = r.json()
    assert b["new"] == 1
    assert b["extraction"]["status"] == "error"
    assert "LLM 不可达" in b["extraction"]["error"]


def test_upload_extract_skipped_when_llm_unconfigured(monkeypatch, _fake_extract):
    from app.intel.api import settings as api_settings

    monkeypatch.setattr(api_settings, "INTEL_LLM_BASE_URL", "", raising=True)
    b = _post([("files", ("a.html", HTML, "text/html"))],
              source_name=SRC, extract=True).json()
    assert b["extraction"]["status"] == "skipped"
    assert _fake_extract == []


def test_upload_extract_ignored_in_dry_run(_fake_extract):
    """dry-run 没入库，无从抽取"""
    b = _post([("files", ("a.html", HTML, "text/html"))],
              source_name=SRC, extract=True, dry_run=True).json()
    assert b["extraction"] is None
    assert _fake_extract == []
