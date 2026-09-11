"""P4-2 微信目录 watch 测试

单元（无 DB）：候选扫描 / cooldown / 子目录分组 / 归档移动（mock ingest）
集成（真实 PG）：单文件经 run_wechat_watch 落 intel.documents 并清理
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import app.intel.service as SVC
from app.intel.ingest.manual import _SUPPORTED_EXT


# --------------------------------------------------------------------------
# 单元：候选扫描
# --------------------------------------------------------------------------
def test_candidates_excludes_processed_and_unsupported(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.html").write_text("<p>x</p>", encoding="utf-8")
    (inbox / "b.txt").write_text("hello", encoding="utf-8")
    (inbox / "c.pdf").write_text("skip", encoding="utf-8")  # 不支持
    proc = inbox / "processed"
    proc.mkdir()
    (proc / "old.html").write_text("<p>y</p>", encoding="utf-8")  # 已归档
    # 子目录下的支持文件也要被扫到
    sub = inbox / "公众号A"
    sub.mkdir()
    (sub / "d.html").write_text("<p>z</p>", encoding="utf-8")

    cands = SVC._wechat_inbox_candidates(inbox, cooldown_sec=0.0)
    names = sorted(p.name for p in cands)
    assert names == ["a.html", "b.txt", "d.html"]  # pdf 与 processed 都被排除


def test_candidates_skips_recently_modified(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    f_old = inbox / "old.html"
    f_old.write_text("<p>old</p>", encoding="utf-8")
    f_new = inbox / "new.html"
    f_new.write_text("<p>new</p>", encoding="utf-8")
    now = time.time()
    # 老文件 mtime 设到 100s 前；新文件 mtime 设为现在（正在写入）
    os.utime(f_old, (now - 100, now - 100))
    os.utime(f_new, (now, now))

    cands = SVC._wechat_inbox_candidates(inbox, cooldown_sec=2.0)
    names = sorted(p.name for p in cands)
    assert names == ["old.html"]  # new.html 因 cooldown 被跳过


def test_candidates_missing_inbox(tmp_path):
    assert SVC._wechat_inbox_candidates(tmp_path / "nope", cooldown_sec=0) == []


# --------------------------------------------------------------------------
# 单元：分组 + 归档（mock ingest，不碰 DB）
# --------------------------------------------------------------------------
def test_watch_groups_by_subdir_and_archives(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "stray.html").write_text("<p>flat</p>", encoding="utf-8")
    sub = inbox / "公众号A"
    sub.mkdir()
    (sub / "a1.html").write_text("<p>a1</p>", encoding="utf-8")
    (sub / "a2.html").write_text("<p>a2</p>", encoding="utf-8")

    calls = []

    def fake_ingest(target, *, source_name=None, limit=None):
        calls.append((str(target), source_name))
        return {"new": 1, "dup": 0, "reposts": 0, "fetched": 1,
                "source": source_name, "last_item_at": None}

    monkeypatch.setattr(SVC, "run_wechat_ingest", fake_ingest)

    summary = SVC.run_wechat_watch(
        inbox=inbox, once=True, default_source="wechat-inbox", cooldown_sec=0.0,
    )
    # 扁平 stray.html → 默认源；a1/a2 → 公众号A
    srcs = sorted(c[1] for c in calls)
    assert srcs == ["wechat-inbox", "公众号A", "公众号A"]
    # 全部归档到 processed/
    assert (inbox / "processed" / "stray.html").exists()
    assert (inbox / "processed" / "公众号A" / "a1.html").exists()
    assert (inbox / "processed" / "公众号A" / "a2.html").exists()
    # 原位已清空
    assert not (inbox / "stray.html").exists()
    assert summary["new"] == 3


def test_watch_missing_inbox_returns_gracefully(tmp_path):
    summary = SVC.run_wechat_watch(inbox=tmp_path / "nope", once=True)
    assert summary["exists"] is False
    assert summary["files"] == 0


def test_watch_ingest_failure_leaves_file(tmp_path, monkeypatch):
    """单文件 ingest 抛错 → 留原地等下轮重试，不拖垮整轮，不归档"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "bad.html").write_text("<p>bad</p>", encoding="utf-8")

    def fake_ingest(target, *, source_name=None, limit=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(SVC, "run_wechat_ingest", fake_ingest)

    summary = SVC.run_wechat_watch(
        inbox=inbox, once=True, default_source="wechat-inbox", cooldown_sec=0.0,
    )
    assert summary["errors"] == 1
    assert (inbox / "bad.html").exists()  # 未归档
    assert not (inbox / "processed").exists()


# --------------------------------------------------------------------------
# 集成：真实落库（清理）
# --------------------------------------------------------------------------
def test_watch_real_ingest_and_cleanup(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    html = inbox / "招商三季报.html"
    html.write_text(
        '<!DOCTYPE html><html><head>'
        '<meta property="og:title" content="招行三季报：零售修复超预期">'
        '<meta property="article:author" content="银行观察">'
        '<meta property="article:published_time" content="2024-10-28T10:00:00+08:00">'
        '</head><body><article><p>招商银行三季度净息差环比改善。</p></article></body></html>',
        encoding="utf-8",
    )
    try:
        summary = SVC.run_wechat_watch(
            inbox=inbox, once=True, default_source="pytest_wechat", cooldown_sec=0.0,
        )
        assert summary["new"] >= 1
        # 回查落库
        from app.intel import store
        with store.get_engine().connect() as c:
            row = c.execute(
                store.text(
                    "SELECT title, author, source_id FROM intel.documents "
                    "WHERE source_id=(SELECT id FROM intel.sources WHERE url=:u)"
                ),
                {"u": "wechat://pytest_wechat"},
            ).mappings().first()
            assert row is not None
            assert row["title"] == "招行三季报：零售修复超预期"
            assert row["author"] == "银行观察"
        # 归档
        assert (inbox / "processed" / "招商三季报.html").exists()
    finally:
        from app.intel import store
        with store.get_engine().begin() as c:
            c.execute(
                store.text("DELETE FROM intel.documents WHERE source_id="
                           "(SELECT id FROM intel.sources WHERE url=:u)"),
                {"u": "wechat://pytest_wechat"},
            )
            c.execute(store.text("DELETE FROM intel.sources WHERE url=:u"),
                      {"u": "wechat://pytest_wechat"})
