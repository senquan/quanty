"""每日构建编排（app.intel.daily_build）与 19:30 定时任务注册测试

打桩策略：三步各自用 monkeypatch 替换（``_run_extract`` / ``_run_profiles`` /
``build_factors``），**不真跑 LLM、不真算全量因子**，只验编排与容错契约：
- 步骤互相隔离（一步挂了后续照跑）
- LLM 未配置 = skipped（不是失败）
- 预算中止、异常、空结果都要如实写进摘要
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.config import settings
from app.intel import daily_build, tasks


# ----------------------------------------------------------------- 桩

class _Boom(Exception):
    pass


@pytest.fixture
def stub_steps(monkeypatch):
    """把三步换成可控桩，返回记录调用的 dict"""
    calls = {"extract": [], "profiles": [], "factors": []}

    def _extract(limit: int) -> dict:
        calls["extract"].append(limit)
        return {
            "candidates": 10, "prescreen_hit": 4, "prescreen_miss": 6,
            "extracted": 3, "quarantined": 0, "no_mention": 1,
            "api_fail": 0, "cost_cny": 0.0312, "stopped_reason": None,
        }

    def _profiles(prompt_version: str, profile_version: str, engine=None) -> dict:
        calls["profiles"].append((prompt_version, profile_version))
        return {"profiles": 11, "sample_sufficient": 3, "sample_insufficient": 8}

    def _factors(*, prompt_version: str, engine=None, dry_run: bool = False) -> dict:
        calls["factors"].append((prompt_version, dry_run))
        return {
            "mentions": 378, "rows": 1764, "written": 0 if dry_run else 1764,
            "symbols": 277, "trade_dates": 2, "calendar_extended": True,
            "anti_lookahead_violations": 0,
            "codes": ["INTL_STYLE_MATCH", "INTL_MENTION_HEAT_5"],
            "by_code": {},
        }

    monkeypatch.setattr(daily_build, "_run_extract", _extract)
    monkeypatch.setattr(daily_build, "_run_profiles", _profiles)
    monkeypatch.setattr(daily_build, "build_factors", _factors)
    return calls


@pytest.fixture
def llm_on(monkeypatch):
    monkeypatch.setattr(daily_build, "_llm_configured", lambda: True)


@pytest.fixture
def llm_off(monkeypatch):
    monkeypatch.setattr(daily_build, "_llm_configured", lambda: False)


# ----------------------------------------------------------------- 编排

def test_all_three_steps_run(stub_steps, llm_on):
    out = daily_build.run_daily_build()
    assert set(out["steps"]) == {"extract", "profiles", "factors"}
    assert all(s["status"] == "ok" for s in out["steps"].values()), out
    assert out["cost_cny"] == pytest.approx(0.0312)
    assert out["errors"] == []
    # factor_codes 按六因子固定顺序排列（不是数据库里的随机顺序）
    assert out["factor_codes"] == ["INTL_MENTION_HEAT_5", "INTL_STYLE_MATCH"]
    assert stub_steps["extract"] == [
        int(getattr(settings, "INTEL_DAILY_BUILD_EXTRACT_LIMIT", 200))
    ]


def test_extract_skipped_when_llm_unconfigured(stub_steps, llm_off):
    out = daily_build.run_daily_build()
    ex = out["steps"]["extract"]
    assert ex["status"] == "skipped"
    assert "INTEL_LLM_*" in ex["reason"]
    # 未配置 LLM 不是错误，画像/因子（零 LLM）照跑
    assert out["steps"]["profiles"]["status"] == "ok"
    assert out["steps"]["factors"]["status"] == "ok"
    assert out["errors"] == []
    assert stub_steps["extract"] == []  # 一次都没调用抽取


def test_extract_error_does_not_stop_later_steps(stub_steps, llm_on, monkeypatch):
    def _boom(limit: int) -> dict:
        raise _Boom("vllm 502")

    monkeypatch.setattr(daily_build, "_run_extract", _boom)
    out = daily_build.run_daily_build()
    assert out["steps"]["extract"]["status"] == "error"
    assert "Boom" in out["steps"]["extract"]["error"]
    assert len(out["errors"]) == 1 and out["errors"][0].startswith("extract:")
    # 关键：抽取挂了，画像与因子仍然刷新
    assert out["steps"]["profiles"]["status"] == "ok"
    assert out["steps"]["factors"]["status"] == "ok"


def test_profiles_error_does_not_stop_factors(stub_steps, llm_on, monkeypatch):
    monkeypatch.setattr(
        daily_build, "_run_profiles",
        lambda pv, pver, engine=None: (_ for _ in ()).throw(_Boom("pg down")),
    )
    out = daily_build.run_daily_build()
    assert out["steps"]["profiles"]["status"] == "error"
    assert out["steps"]["factors"]["status"] == "ok"
    assert out["steps"]["extract"]["status"] == "ok"


def test_budget_stopped_is_not_ok(stub_steps, llm_on, monkeypatch):
    monkeypatch.setattr(
        daily_build, "_run_extract",
        lambda limit: {"cost_cny": 9.9, "stopped_reason": "budget", "extracted": 2},
    )
    out = daily_build.run_daily_build()
    assert out["steps"]["extract"]["status"] == "budget_stopped"
    assert out["cost_cny"] == pytest.approx(9.9)


def test_empty_factor_rows_yield_no_codes(stub_steps, llm_on, monkeypatch):
    monkeypatch.setattr(
        daily_build, "build_factors",
        lambda *, prompt_version, engine=None, dry_run=False: {
            "rows": 0, "written": 0, "symbols": 0, "trade_dates": 0,
            "anti_lookahead_violations": 0, "codes": [], "by_code": {},
        },
    )
    out = daily_build.run_daily_build()
    assert out["factor_codes"] == []


def test_selected_steps_can_be_skipped(stub_steps, llm_on):
    out = daily_build.run_daily_build(do_extract=False, do_factors=False)
    assert out["steps"]["extract"]["status"] == "skipped"
    assert out["steps"]["factors"]["status"] == "skipped"
    assert out["steps"]["profiles"]["status"] == "ok"


def test_extract_limit_override(stub_steps, llm_on):
    daily_build.run_daily_build(extract_limit=7)
    assert stub_steps["extract"] == [7]


def test_format_summary_mentions_all_steps(stub_steps, llm_on):
    s = daily_build.format_summary(daily_build.run_daily_build())
    assert "抽取[ok]" in s and "画像[ok]" in s and "因子[ok]" in s


# ----------------------------------------------------------------- build_factors 自检

def test_build_factors_counts_violations(monkeypatch):
    """防前视违例要被计数（而不是静默写库）"""
    from datetime import date, datetime, timezone

    from app.intel.factorize import factors as F

    def _rows(mentions, calendar, profiles, factor_version="v1", prompt_version="v2"):
        return [
            {"symbol": "600519.SH", "trade_date": date(2026, 9, 8),
             "factor_code": "INTL_MENTION_HEAT_5", "value": 1.0,
             "available_at": datetime(2026, 9, 8, 3, 0, tzinfo=timezone.utc)},
            {"symbol": "000858.SZ", "trade_date": date(2026, 9, 8),
             "factor_code": "INTL_MENTION_HEAT_5", "value": 2.0,
             "available_at": datetime(2026, 9, 9, 3, 0, tzinfo=timezone.utc)},  # 违例
        ]

    monkeypatch.setattr(F, "build_all_factor_rows", _rows)
    monkeypatch.setattr(F, "load_trade_calendar", lambda engine=None: [date(2026, 9, 8)])
    monkeypatch.setattr(F, "load_mentions", lambda pv, engine=None: [{"id": 1}])
    monkeypatch.setattr(F, "load_profile_index", lambda engine=None: {})

    res = daily_build.build_factors(prompt_version="v2", dry_run=True)
    assert res["rows"] == 2
    assert res["anti_lookahead_violations"] == 1
    assert res["written"] == 0 and res["dry_run"] is True
    assert res["codes"] == ["INTL_MENTION_HEAT_5"]
    assert res["by_code"]["INTL_MENTION_HEAT_5"]["rows"] == 2
    assert res["by_code"]["INTL_MENTION_HEAT_5"]["mean"] == pytest.approx(1.5)


# ----------------------------------------------------------------- 定时任务

def _mk_scheduler():
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    return AsyncIOScheduler()


def test_register_daily_build_job_default(monkeypatch):
    monkeypatch.setattr(settings, "INTEL_DAILY_BUILD_ENABLED", True)
    monkeypatch.setattr(settings, "INTEL_DAILY_BUILD_HOUR", 19)
    monkeypatch.setattr(settings, "INTEL_DAILY_BUILD_MINUTE", 30)
    sch = _mk_scheduler()
    tasks.register_intel_jobs(sch)
    job = sch.get_job("intel_daily_build")
    assert job is not None
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["hour"] == "19" and fields["minute"] == "30"
    assert "fri" in fields["day_of_week"].lower() or "5" in fields["day_of_week"]
    assert sch.get_job("intel_rss_poll") is not None


def test_register_daily_build_job_honours_config(monkeypatch):
    monkeypatch.setattr(settings, "INTEL_DAILY_BUILD_ENABLED", True)
    monkeypatch.setattr(settings, "INTEL_DAILY_BUILD_HOUR", 21)
    monkeypatch.setattr(settings, "INTEL_DAILY_BUILD_MINUTE", 5)
    sch = _mk_scheduler()
    tasks.register_intel_jobs(sch)
    fields = {f.name: str(f) for f in sch.get_job("intel_daily_build").trigger.fields}
    assert fields["hour"] == "21" and fields["minute"] == "5"


def test_register_daily_build_disabled(monkeypatch):
    monkeypatch.setattr(settings, "INTEL_DAILY_BUILD_ENABLED", False)
    sch = _mk_scheduler()
    tasks.register_intel_jobs(sch)
    assert sch.get_job("intel_daily_build") is None
    assert sch.get_job("intel_rss_poll") is not None  # RSS 轮询不受影响


def test_job_emits_factor_updated(stub_steps, llm_on, monkeypatch):
    """定时任务：构建完回到事件循环线程广播 factor_updated"""
    emitted = {}

    def _fake_emit(codes, *, version=None, reason=None):
        emitted["codes"] = list(codes)
        emitted["reason"] = reason

    monkeypatch.setattr("app.ws.events.emit_factor_updated", _fake_emit)
    monkeypatch.setattr(settings, "INTEL_DAILY_BUILD_EMIT_WS", True)
    monkeypatch.setattr(
        daily_build, "run_daily_build",
        lambda **kw: {
            "started_at": "t0", "finished_at": "t1", "steps": {}, "errors": [],
            "cost_cny": 0.0, "factor_codes": ["INTL_MENTION_HEAT_5"], "emitted": False,
        },
    )
    asyncio.run(tasks._daily_intel_build_job())
    assert emitted["codes"] == ["INTL_MENTION_HEAT_5"]
    assert emitted["reason"] == "intel_daily_build"


def test_job_no_emit_when_no_rows(monkeypatch):
    """没有因子行就别广播（避免 backend 收到空 code 列表）"""
    emitted = []

    monkeypatch.setattr("app.ws.events.emit_factor_updated",
                        lambda codes, **kw: emitted.append(list(codes)))
    monkeypatch.setattr(
        daily_build, "run_daily_build",
        lambda **kw: {"steps": {}, "errors": [], "cost_cny": 0.0, "factor_codes": []},
    )
    asyncio.run(tasks._daily_intel_build_job())
    assert emitted == []


def test_job_emit_disabled(monkeypatch):
    emitted = []
    monkeypatch.setattr("app.ws.events.emit_factor_updated",
                        lambda codes, **kw: emitted.append(list(codes)))
    monkeypatch.setattr(settings, "INTEL_DAILY_BUILD_EMIT_WS", False)
    monkeypatch.setattr(
        daily_build, "run_daily_build",
        lambda **kw: {"steps": {}, "errors": [], "cost_cny": 0.0,
                      "factor_codes": ["INTL_MENTION_HEAT_5"]},
    )
    asyncio.run(tasks._daily_intel_build_job())
    assert emitted == []


def test_job_survives_build_exception(monkeypatch):
    """构建整体抛异常不能打死 scheduler（只记日志）"""
    monkeypatch.setattr(
        daily_build, "run_daily_build",
        lambda **kw: (_ for _ in ()).throw(_Boom("boom")),
    )
    asyncio.run(tasks._daily_intel_build_job())  # 不抛即可
