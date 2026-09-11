"""P4-4 作者风格总结测试

单元（无 DB / 无 LLM）：prompt 构造 / JSON 解析校验（围栏、必填、标签白名单、置信度夹紧）
集成（真实 PG）：upsert + latest_summaries 往返；mock LLM 跑通 build_style_summaries 落库
"""
from __future__ import annotations

import json

import pytest

from app.intel.aggregate import style_summary as SS


# --------------------------------------------------------------------------
# 单元：prompt 构造
# --------------------------------------------------------------------------
FAKE_PROFILE = {
    "profile_key": "张三", "profile_type": "author",
    "total_mentions": 42, "total_docs": 20, "unique_symbols": 9,
    "date_first": "2026-01-01", "date_last": "2026-09-01",
    "stance_dist": json.dumps({"bullish": 20, "neutral": 15, "bearish": 7}),
    "style_vector": json.dumps({"bullish_ratio": 0.5}),
    "top_symbols": json.dumps([{"symbol": "600519.SH", "count": 8}]),
    "horizon_dist": json.dumps({"mid": 30, "event": 12}),
    "avg_excess_20d": 1.23, "accuracy_sample_size": 30, "win_rate_20d": 0.55,
    "sample_insufficient": False, "drift_detected": False,
}

FAKE_MENTIONS = [
    {"symbol": "600519.SH", "stance": "bullish", "horizon": "mid",
     "thesis": "三季度动销回暖", "title": "茅台跟踪", "published_at": "2026-08-01"},
    {"symbol": "601318.SH", "stance": "neutral", "horizon": "event",
     "thesis": "平安回购", "title": "保险观察", "published_at": "2026-07-01"},
]


def test_build_prompt_contains_key_and_stats():
    system, user = SS.build_style_prompt(FAKE_PROFILE, FAKE_MENTIONS)
    assert "张三" in user
    assert "600519.SH" in user
    assert "观点数 42" in user
    assert "三季度动销回暖" in user
    assert system  # 系统提示非空


def test_build_prompt_handles_empty_mentions():
    _, user = SS.build_style_prompt(FAKE_PROFILE, [])
    assert "（无样本）" in user


# --------------------------------------------------------------------------
# 单元：解析与校验
# --------------------------------------------------------------------------
VALID = json.dumps({
    "summary": "该作者偏好白酒与银行龙头，以基本面为主，偏中线持有，观点偏多。",
    "style_tags": ["价值投资", "龙头偏好", "自创标签"],
    "sectors": ["白酒", "银行"],
    "holding_period": "mid",
    "conviction": "medium",
    "caveats": "样本集中在 2026 年。",
    "confidence": 0.8,
}, ensure_ascii=False)


def test_parse_valid():
    d = SS.parse_style_summary(VALID)
    assert d["summary"].startswith("该作者偏好")
    # 自创标签被白名单过滤
    assert d["style_tags"] == ["价值投资", "龙头偏好"]
    assert d["sectors"] == ["白酒", "银行"]
    assert d["holding_period"] == "mid"
    assert d["conviction"] == "medium"
    assert d["confidence"] == 0.8


def test_parse_strips_markdown_fence():
    raw = "```json\n" + VALID + "\n```"
    d = SS.parse_style_summary(raw)
    assert d["holding_period"] == "mid"


def test_parse_missing_summary_raises():
    with pytest.raises(ValueError):
        SS.parse_style_summary(json.dumps({"style_tags": []}))


def test_parse_short_summary_raises():
    with pytest.raises(ValueError):
        SS.parse_style_summary(json.dumps({"summary": "短"}))


def test_parse_invalid_enum_and_clamps_confidence():
    raw = json.dumps({
        "summary": "这是一个足够长的风格综述文本。",
        "holding_period": "十年", "conviction": "极高", "confidence": 5.0,
    }, ensure_ascii=False)
    d = SS.parse_style_summary(raw)
    assert d["holding_period"] is None
    assert d["conviction"] is None
    assert d["confidence"] == 1.0  # 夹紧到 1


def test_parse_negative_confidence_clamped():
    raw = json.dumps({"summary": "足够长的综述文本。", "confidence": -2}, ensure_ascii=False)
    assert SS.parse_style_summary(raw)["confidence"] == 0.0


def test_parse_no_json_raises():
    with pytest.raises(ValueError):
        SS.parse_style_summary("抱歉，我无法完成")


# --------------------------------------------------------------------------
# 集成：mock LLM 跑通落库
# --------------------------------------------------------------------------
class _Resp:
    content = VALID
    input_tokens = 1200
    output_tokens = 300
    cost_cny = 0.0123
    latency_ms = 1500
    model = "test-model"


class _FakeClient:
    def __init__(self):
        self.calls = []

    def extract_once(self, system, user, doc_id=None, prompt_version=None):
        self.calls.append({"doc_id": doc_id, "prompt_version": prompt_version})
        return _Resp()


def test_build_style_summaries_mocked(monkeypatch):
    """mock profile 加载 + mention 加载 + LLM，跑通全链路并落真实库"""
    from app.intel import store

    engine = store.get_engine()
    SS.ensure_style_summary_table(engine)

    monkeypatch.setattr(SS, "load_profiles", lambda pv, engine=None: [dict(FAKE_PROFILE)])
    monkeypatch.setattr(SS, "fetch_author_mentions",
                        lambda *a, **k: [dict(m) for m in FAKE_MENTIONS])

    client = _FakeClient()
    try:
        out = SS.build_style_summaries(client=client, min_mentions=3)
        assert out["ok"] == 1
        assert out["cost_cny"] == pytest.approx(0.0123)
        # prompt_version 必须传 style_v1（与抽取 v2 分开归因）
        assert client.calls[0]["prompt_version"] == SS.STYLE_PROMPT_VERSION
        assert client.calls[0]["doc_id"] is None

        rows = SS.latest_summaries(engine)
        mine = [r for r in rows if r["profile_key"] == "张三"]
        assert mine, "应落库一条总结"
        r = mine[0]
        assert r["status"] == "ok"
        assert r["summary"].startswith("该作者偏好")
        # JSONB 列由驱动直接反序列化为 Python 对象，无需再 json.loads
        assert r["style_tags"] == ["价值投资", "龙头偏好"]
        assert r["holding_period"] == "mid"
        assert r["cost_cny"] == pytest.approx(0.0123)
    finally:
        with engine.begin() as c:
            c.execute(store.text(
                "DELETE FROM intel.author_style_summaries WHERE profile_key = :k"),
                {"k": "张三"})


def test_build_style_summaries_skips_low_sample(monkeypatch):
    """mentions < min_mentions → 跳过，不调 LLM，status=skipped"""
    from app.intel import store

    engine = store.get_engine()
    SS.ensure_style_summary_table(engine)

    low = dict(FAKE_PROFILE)
    low["total_mentions"] = 1
    monkeypatch.setattr(SS, "load_profiles", lambda pv, engine=None: [low])
    monkeypatch.setattr(SS, "fetch_author_mentions", lambda *a, **k: [])

    client = _FakeClient()
    try:
        out = SS.build_style_summaries(client=client, min_mentions=3)
        assert out["skipped"] == 1
        assert out["ok"] == 0
        assert client.calls == []  # 未调 LLM
        rows = [r for r in SS.latest_summaries(engine) if r["profile_key"] == "张三"]
        assert rows and rows[0]["status"] == "skipped"
        assert rows[0]["summary"] is None
    finally:
        with engine.begin() as c:
            c.execute(store.text(
                "DELETE FROM intel.author_style_summaries WHERE profile_key = :k"),
                {"k": "张三"})


def test_dry_run_does_not_call_llm(monkeypatch):
    monkeypatch.setattr(SS, "load_profiles", lambda pv, engine=None: [dict(FAKE_PROFILE)])
    monkeypatch.setattr(SS, "fetch_author_mentions",
                        lambda *a, **k: [dict(m) for m in FAKE_MENTIONS])
    client = _FakeClient()
    out = SS.build_style_summaries(client=client, dry_run=True)
    assert client.calls == []
    assert out["details"][0]["status"] == "dry_run"
    assert out["ok"] == 0


def test_dry_run_marks_would_skip(monkeypatch):
    """dry-run 也应如实反映样本红线：低于 min_mentions 的标 dry_run_skip 而非 dry_run"""
    low = dict(FAKE_PROFILE)
    low["total_mentions"] = 1
    monkeypatch.setattr(SS, "load_profiles", lambda pv, engine=None: [low])
    monkeypatch.setattr(SS, "fetch_author_mentions", lambda *a, **k: [])

    client = _FakeClient()
    out = SS.build_style_summaries(client=client, dry_run=True, min_mentions=3)
    assert client.calls == []
    assert out["details"][0]["status"] == "dry_run_skip"
    assert out["skipped"] == 1
    assert out["ok"] == 0


def test_api_fail_does_not_overwrite_existing_ok(monkeypatch):
    """已有 ok 总结时，LLM 不可达（api_fail）不得把好数据洗掉

    场景：每周自动化定时跑，某次 vLLM 上游 502 —— 不应让前端画像卡片变空白。
    """
    from app.intel import store

    engine = store.get_engine()
    SS.ensure_style_summary_table(engine)

    monkeypatch.setattr(SS, "load_profiles", lambda pv, engine=None: [dict(FAKE_PROFILE)])
    monkeypatch.setattr(SS, "fetch_author_mentions",
                        lambda *a, **k: [dict(m) for m in FAKE_MENTIONS])

    ok_rec = {
        "profile_key": "张三", "profile_type": "author",
        "summary_version": SS.SUMMARY_VERSION, "prompt_version": SS.STYLE_PROMPT_VERSION,
        "summary": "该作者偏好价值投资。", "style_tags": json.dumps(["价值投资"]),
        "sectors": json.dumps(["白酒"]), "holding_period": "mid",
        "conviction": "medium", "caveats": "样本偏少", "confidence": 0.7,
        "input_stats": json.dumps({"total_mentions": 42}, ensure_ascii=False),
        "sample_size": 40, "model": "m", "input_tokens": 100, "output_tokens": 50,
        "cost_cny": 0.01, "latency_ms": 100, "status": "ok", "error": None,
    }
    assert SS.upsert_style_summary(ok_rec, engine) is True

    class DownClient:
        def extract_once(self, system, user, doc_id=None, prompt_version=None):
            raise RuntimeError("HTTP 502: upstream connect failed")

    try:
        out = SS.build_style_summaries(client=DownClient())
        assert out["failed"] == 1
        assert out["kept_previous"] == 1
        assert out["details"][0]["status"] == "api_fail_kept_previous"
        rows = [r for r in SS.latest_summaries(engine) if r["profile_key"] == "张三"]
        assert rows and rows[0]["status"] == "ok"
        assert rows[0]["summary"] == "该作者偏好价值投资。"
    finally:
        with engine.begin() as c:
            c.execute(store.text(
                "DELETE FROM intel.author_style_summaries WHERE profile_key = :k"),
                {"k": "张三"})


def test_upsert_protect_ok_can_be_disabled(monkeypatch):
    """protect_ok=False 时允许失败覆盖（供人工强制重算）"""
    from app.intel import store

    engine = store.get_engine()
    SS.ensure_style_summary_table(engine)
    try:
        SS.upsert_style_summary({
            "profile_key": "李四", "profile_type": "author",
            "summary_version": SS.SUMMARY_VERSION,
            "prompt_version": SS.STYLE_PROMPT_VERSION,
            "summary": "旧的好总结", "style_tags": json.dumps([]),
            "sectors": json.dumps([]), "holding_period": None, "conviction": None,
            "caveats": None, "confidence": None, "input_stats": json.dumps({}),
            "sample_size": 0, "model": None, "input_tokens": 0, "output_tokens": 0,
            "cost_cny": 0.0, "latency_ms": 0, "status": "ok", "error": None,
        }, engine)
        written = SS.upsert_style_summary({
            "profile_key": "李四", "profile_type": "author",
            "summary_version": SS.SUMMARY_VERSION,
            "prompt_version": SS.STYLE_PROMPT_VERSION,
            "summary": None, "style_tags": json.dumps([]), "sectors": json.dumps([]),
            "holding_period": None, "conviction": None, "caveats": None,
            "confidence": None, "input_stats": json.dumps({}), "sample_size": 0,
            "model": None, "input_tokens": 0, "output_tokens": 0, "cost_cny": 0.0,
            "latency_ms": 0, "status": "api_fail", "error": "boom",
        }, engine, protect_ok=False)
        assert written is True
        rows = [r for r in SS.latest_summaries(engine) if r["profile_key"] == "李四"]
        assert rows and rows[0]["status"] == "api_fail"
    finally:
        with engine.begin() as c:
            c.execute(store.text(
                "DELETE FROM intel.author_style_summaries WHERE profile_key = :k"),
                {"k": "李四"})


def test_schema_fail_recorded(monkeypatch):
    """LLM 返回不合规则文本 → status=schema_fail，成本仍记账"""
    from app.intel import store

    engine = store.get_engine()
    SS.ensure_style_summary_table(engine)

    monkeypatch.setattr(SS, "load_profiles", lambda pv, engine=None: [dict(FAKE_PROFILE)])
    monkeypatch.setattr(SS, "fetch_author_mentions", lambda *a, **k: [])

    class _BadClient:
        def extract_once(self, system, user, doc_id=None, prompt_version=None):
            return _Resp.__class__("bad", content="不好意思我不会")

    class BadResp:
        content = "这不是 JSON"
        input_tokens = 100
        output_tokens = 20
        cost_cny = 0.001
        latency_ms = 100
        model = "m"

    class BadClient:
        def extract_once(self, system, user, doc_id=None, prompt_version=None):
            return BadResp()

    try:
        out = SS.build_style_summaries(client=BadClient())
        assert out["failed"] == 1
        assert out["details"][0]["status"] == "schema_fail"
        assert out["cost_cny"] == pytest.approx(0.001)  # 失败也记成本（钱花了）
        rows = [r for r in SS.latest_summaries(engine) if r["profile_key"] == "张三"]
        assert rows and rows[0]["status"] == "schema_fail"
    finally:
        with engine.begin() as c:
            c.execute(store.text(
                "DELETE FROM intel.author_style_summaries WHERE profile_key = :k"),
                {"k": "张三"})
