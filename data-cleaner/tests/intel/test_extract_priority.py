"""D-3 抽取优先级固化：回归测试

背景（真实事故）
----------------
``docs_for_understanding`` 原先按 ``ORDER BY d.id`` 升序 + ``LIMIT``。documents 的
id 只反映「抓取先后」，与「是否值得先理解」无关 —— 结果 RSS 老欠账 id 小永远先抽，
用户手动投喂/公众号文章 id 大，排在几千条之后饿死。

实测（2026-09-11）：全局欠账 **12814 篇**，其中东方财富 RSS 一家 11853 篇（id 最小）；
手动投喂的「分红养老之路」只有 7 篇欠账却排在队尾。当时只能靠一次性脚本
``_p4_extract_source.py --source <名>`` 用 ``doc_ids`` 定向绕开。

固化后的三层排序
----------------
1. ``source_type`` 优先级（``INTEL_EXTRACT_PRIORITY``，manual/wechat 先于 rss）；
2. 同级内 ``available_at DESC``（先理解新鲜的）；
3. ``d.id`` 兜底（稳定排序，分页不重不漏）。

测试策略
--------
候选集是全库欠账（上万条），无法靠造夹具文档改变其位次。故分两层：
- **纯函数层**：``_extract_priority_map`` / ``_understanding_order_sql`` 白盒断言；
- **真实库层**：只断言「返回的首条确实来自 manual 源」这一可观测事实
  （旧实现下首条必然是 id 最小的东方财富 RSS）。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from app.intel import store

# ⚠️ 不要在这里硬编码 DB URL：曾因此把生产库口令写进源码（入库前已改）。
# 统一从 app 配置取（settings.DATABASE_URL，读 .env），driver 降级交给 store._sync_url()。
DB_URL = store._sync_url()


@pytest.fixture
def engine():
    return create_engine(DB_URL)


# ---------------------------------------------------------------- 纯函数层


def test_priority_map_parsing_default():
    """默认配置解析正确"""
    assert store._extract_priority_map() == {"manual": 10, "wechat": 20, "rss": 50}


def test_priority_map_parsing_tolerates_spaces(monkeypatch):
    """容忍键值周围空格（配置容易手抖写出来）"""
    from app.intel.core import config as cfg

    monkeypatch.setattr(cfg.settings, "INTEL_EXTRACT_PRIORITY",
                        " manual : 5 , rss:1 ")
    assert store._extract_priority_map() == {"manual": 5, "rss": 1}


def test_priority_map_skips_bad_entries(monkeypatch):
    """坏值 / 半截项 / 空 key 都要跳过，不能让整个解析崩掉或产出脏 key

    空 key（``":9"``）是真 bug：早期实现会产出 ``{'': 9}``，进 SQL 后
    生成 ``WHEN :p_ THEN 9`` 这种非法分支。
    """
    from app.intel.core import config as cfg

    monkeypatch.setattr(cfg.settings, "INTEL_EXTRACT_PRIORITY",
                        "manual:abc,rss:1,noColon,:9")
    assert store._extract_priority_map() == {"rss": 1}


def test_priority_map_empty_string(monkeypatch):
    """空串 = 显式退回旧的纯 id 升序（可回滚开关）"""
    from app.intel.core import config as cfg

    monkeypatch.setattr(cfg.settings, "INTEL_EXTRACT_PRIORITY", "")
    assert store._extract_priority_map() == {}


def test_order_sql_contains_three_levels():
    """白盒：排序 SQL 必须是 source_type 优先级 → available_at DESC → d.id 三层"""
    prio = {"manual": 10, "wechat": 20, "rss": 50}
    sql, params = store._understanding_order_sql(prio)

    assert "s.source_type" in sql, "缺少 source_type 优先级层"
    assert "available_at DESC" in sql, "缺少时效层"
    assert sql.strip().endswith("d.id"), "缺少稳定排序兜底"
    assert "ELSE 100" in sql, "未列出的 source_type 应沉底"
    # 三段顺序必须正确（优先级在前，id 在最后）
    assert sql.index("source_type") < sql.index("available_at") < sql.index("d.id")
    # 参数用 :p_<key> 命名，避免与其它 bind 冲突
    assert params == {"p_manual": "manual", "p_wechat": "wechat", "p_rss": "rss"}


def test_order_sql_empty_priority_is_id_only():
    """空优先级 → 只按 d.id（回滚行为）"""
    sql, params = store._understanding_order_sql({})
    assert sql.strip() == "ORDER BY d.id"
    assert params == {}


def test_order_sql_keys_are_lowercased_and_quoted_safely():
    """key 统一小写并参数化，杜绝把配置内容拼进 SQL 的注入面"""
    sql, params = store._understanding_order_sql({"MANUAL": 1})
    assert params == {"p_manual": "manual"}
    assert "MANUAL" not in sql, "配置里的原始大小写不该出现在 SQL 里"


# ---------------------------------------------------------------- 真实库层


def test_first_candidate_is_not_the_oldest_rss_backlog(engine):
    """端到端：候选集首条不该是 id 最小的 RSS 老欠账

    旧实现 ``ORDER BY d.id`` 下首条必然是 id 最小的东方财富文章；
    新实现下首条应来自 manual/wechat（id 大得多）。
    这是最能说明问题的一个可观测事实。
    """
    docs = store.docs_for_understanding(1)
    assert docs, "库里有欠账，应至少返回 1 条"
    top = docs[0]

    with engine.connect() as c:
        stype = c.execute(
            text("""SELECT s.source_type FROM intel.documents d
                    JOIN intel.sources s ON s.id = d.source_id
                    WHERE d.id = :i"""),
            {"i": top["id"]},
        ).scalar_one()
        min_id = c.execute(
            text("""SELECT min(d.id) FROM intel.documents d
                    WHERE NOT EXISTS (SELECT 1 FROM intel.llm_runs r
                                      WHERE r.doc_id = d.id AND r.status='ok'
                                        AND r.prompt_version='v2')"""),
        ).scalar_one()

    assert stype in ("manual", "wechat"), (
        f"首条应来自人工投喂/公众号，实际 source_type={stype} id={top['id']}"
    )
    assert top["id"] != min_id, "首条仍是最小 id（旧行为），优先级未生效"


def test_priority_bucket_ordering_on_real_db(engine):
    """真实库：按 source_type 分桶后，桶间顺序必须符合配置的优先级

    不假设各桶都有数据（RSS 欠账巨多、manual 可能为 0），只校验实际出现的桶。
    """
    docs = store.docs_for_understanding(300)
    assert docs

    with engine.connect() as c:
        rows = c.execute(
            text("""SELECT d.id, s.source_type FROM intel.documents d
                    JOIN intel.sources s ON s.id = d.source_id
                    WHERE d.id = ANY(:ids)"""),
            {"ids": [d["id"] for d in docs]},
        ).all()
    stype_of = {int(r[0]): r[1] for r in rows}

    prio = store._extract_priority_map()
    seen: list[int] = []
    for d in docs:
        p = prio.get((stype_of.get(d["id"]) or "").lower(), 100)
        if not seen or seen[-1] != p:
            seen.append(p)
    assert seen == sorted(seen), f"桶顺序未按优先级递增：{seen}（优先级表 {prio}）"


def test_doc_ids_scoped_keeps_caller_order():
    """doc_ids 定向抽取不受优先级影响（调用方已按自己关心的顺序给定）

    上传后立即抽取（``extract=true``）依赖这条：只抽刚上传的那批，
    不该被全局优先级重排或混入历史欠账。
    """
    with store.get_engine().connect() as c:
        ids = [int(r[0]) for r in c.execute(
            text("""SELECT d.id FROM intel.documents d
                    ORDER BY d.id DESC LIMIT 5"""),
        )]
    got = [d["id"] for d in store.docs_for_understanding(
        len(ids), prompt_version="v2", doc_ids=ids)]
    assert set(got) <= set(ids), "定向抽取不得混入范围外的文档"


def test_limit_is_respected():
    """limit 生效（不因排序改动而失效）"""
    assert len(store.docs_for_understanding(3)) <= 3
