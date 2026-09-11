"""intel 版本契约：backend 硬编码的 PROMPT_VERSION 必须与 data-cleaner 产出一致

背景（D-11）：backend ``endpoints/intel.py`` 用 ``WHERE prompt_version = 'v2'``
过滤前端数据，真实产出方是 data-cleaner 的 ``app/intel/understand/prompts.py``。
两侧都是字面量、跨仓库、无任何守护 —— dc 升到 v3 而 backend 仍是 v2 时，
``/mentions`` 会**静默返回空列表**且两侧测试全绿（与 D-1 同类静默失败）。

本文件把契约钉成两件事：
1. **常量一致**：backend 常量必须出现在 dc 的 prompts 模块里（直接读 dc 源文件
   解析，不 import —— 跨仓库、跨运行时的 import 会带来 venv/依赖耦合）。
2. **分支行为**：``verify_prompt_version()`` 在一致 / 不一致 / 无数据 三种情形下
   的状态码正确，且**永不抛异常**（启动期校验必须只告警不阻断）。
"""
import asyncio
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.api_v1.endpoints.intel import (  # noqa: E402
    PROMPT_VERSION,
    MENTION_DEFAULT_PAGE_SIZE,
    verify_prompt_version,
)

# 仓库布局：<root>/backend/tests/xxx.py → <root>/data-cleaner/app/intel/...
_DC_PROMPTS = (
    Path(__file__).resolve().parents[2]
    / "data-cleaner"
    / "app"
    / "intel"
    / "understand"
    / "prompts.py"
)
_VER_RE = re.compile(r'^PROMPT_VERSION\s*=\s*["\']([^"\']+)["\']', re.M)


def _dc_prompt_version() -> str | None:
    """从 dc 源文件解析 PROMPT_VERSION（不 import，避免跨仓库依赖耦合）"""
    if not _DC_PROMPTS.exists():
        return None
    m = _VER_RE.search(_DC_PROMPTS.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def test_backend_prompt_version_matches_data_cleaner():
    """核心契约：backend 暴露给前端的版本 == dc 实际产出的版本

    dc 升级 prompt 后若忘了同步 backend，本用例会红 —— 这是唯一能在 CI 阶段
    拦住「静默返回空列表」的地方（运行期只有 WARN 日志）。
    """
    dc_ver = _dc_prompt_version()
    if dc_ver is None:
        pytest.skip(f"未找到 data-cleaner prompts 模块，跳过契约校验：{_DC_PROMPTS}")
    assert PROMPT_VERSION == dc_ver, (
        f"版本漂移：backend={PROMPT_VERSION!r} 与 data-cleaner={dc_ver!r} 不一致。"
        f" /intel/mentions 将静默返回空列表，请同步两侧（见 docs/memo D-11）。"
    )


def test_mention_where_pins_the_constant():
    """过滤条件必须用常量而非又一处字面量（否则改常量不生效）"""
    from app.api.api_v1.endpoints.intel import _mention_where

    _, params = _mention_where("", "all", "all")
    assert params["pv"] == PROMPT_VERSION


class TestVerifyPromptVersion:
    """verify_prompt_version 的分支行为

    注：backend venv 未装 pytest-asyncio，故用 asyncio.run 驱动协程，
    保持同步用例形态（避免为此引入新测试依赖）。
    """

    def _stub_session(self, monkeypatch, versions_or_exc):
        """把 AsyncSessionLocal 换成桩：给版本列表（伪造库）或异常"""

        class _FakeResult:
            def __init__(self, versions):
                self._v = versions

            def scalars(self):
                versions = self._v

                class _S:
                    @staticmethod
                    def all():
                        return versions
                return _S()

        class _FakeSession:
            async def execute(self, *a, **kw):
                if isinstance(versions_or_exc, Exception):
                    raise versions_or_exc
                return _FakeResult(versions_or_exc)

            async def close(self):
                pass

        monkeypatch.setattr(
            "app.core.database.AsyncSessionLocal", lambda: _FakeSession()
        )

    def test_ok_when_versions_match(self, monkeypatch):
        """库内最新版本 == 常量 → ok"""
        from app.api.api_v1.endpoints import intel as mod

        monkeypatch.setattr(mod, "PROMPT_VERSION", "v9")
        self._stub_session(monkeypatch, ["v9"])
        out = asyncio.run(mod.verify_prompt_version())
        assert out["status"] == "ok"
        assert out["expected"] == "v9" and out["actual"] == "v9"

    def test_mismatch_when_constant_absent(self, monkeypatch):
        """常量不在库内任何版本里 → mismatch（前端将返回空）"""
        from app.api.api_v1.endpoints import intel as mod

        monkeypatch.setattr(mod, "PROMPT_VERSION", "v99")
        self._stub_session(monkeypatch, ["v1", "v2"])
        out = asyncio.run(mod.verify_prompt_version())
        assert out["status"] == "mismatch"
        assert out["actual"] == "v2"

    def test_stale_when_constant_present_but_outdated(self, monkeypatch):
        """常量仍在库内但非最新（dc 已产出新版）→ stale"""
        from app.api.api_v1.endpoints import intel as mod

        monkeypatch.setattr(mod, "PROMPT_VERSION", "v1")
        self._stub_session(monkeypatch, ["v1", "v2"])
        out = asyncio.run(mod.verify_prompt_version())
        assert out["status"] == "stale"
        assert out["actual"] == "v2"

    def test_never_raises_on_db_error(self, monkeypatch):
        """DB 异常必须被吞成 status=error —— 启动期校验绝不能阻断服务"""
        from app.api.api_v1.endpoints import intel as mod

        self._stub_session(monkeypatch, RuntimeError("db down"))
        out = asyncio.run(mod.verify_prompt_version())  # 不抛即通过
        assert out["status"] == "error"
        assert "db down" in out["error"]

    def test_no_data_is_not_an_error(self, monkeypatch):
        """库未初始化（无任何 prompt_version）→ no_data，不当异常"""
        from app.api.api_v1.endpoints import intel as mod

        self._stub_session(monkeypatch, [])
        out = asyncio.run(mod.verify_prompt_version())
        assert out["status"] == "no_data"
