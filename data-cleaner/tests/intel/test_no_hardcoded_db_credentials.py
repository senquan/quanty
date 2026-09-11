"""防回归：源码里不得硬编码数据库连接串（含口令）。

背景（2026-09-10 审计发现）：
    全仓库扫描发现 **6 处**把生产 Postgres 口令写死在源码里，其中 3 处
    已被 git 跟踪 —— 也就是说口令已经进入版本历史。典型形态：

        # 反面教材
        URL = "postgresql+psycopg2://postgres:<PASSWORD>@127.0.0.1:5432/quant"
        DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pw@host/db")

    口令一旦进源码，最轻是入库泄漏，最重是随 fork/截图/日志扩散；
    而正确做法本来就很短 —— 统一走配置：

        from app.intel.store import _sync_url   # 读 .env（settings.DATABASE_URL）
        URL = _sync_url()

防线：
    静态扫描 data-cleaner/ 下的 .py（排除 tests 自身的反面样例与
    .venv/__pycache__），断言不出现「带 user:password@ 的 postgres URL」。

    允许的例外：本测试文件本身（要写反面样例做说明）、以及显式标了
    `# noqa: hardcoded-db` 的行。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# data-cleaner/ 根（tests/intel/ 往上两级）
PKG_ROOT = Path(__file__).resolve().parents[2]

# 匹配 postgres/postgresql[+driver]://<user>:<password>@ —— 必须同时有 user 和 password。
#
# ⚠️ 只收「真口令」，必须排除两类"看着像但不是泄漏"的写法，否则守卫会被误报淹没：
#   1. 占位符口令（形如 user:pw@ / user:password@ / user:***@ / user:changeme@）
#      —— 常见于 docstring 里的 URL 格式说明、本地默认值；
#   2. pydantic Settings 的默认连接串（本地开发用，非生产）。
# 判定口径：口令段长度 >= 8，且不是纯小写英文占位词，且不含 ***。
_PLACEHOLDER_PW = re.compile(
    r"^(?:\*+|<.*>|x+|pass(?:word)?|pw|secret|changeme|yourpassword|your_password|"
    r"example|test|fake|dummy|placeholder|quant_user|postgres|root|admin)$",
    re.IGNORECASE,
)
_CRED_URL = re.compile(
    r"postgres(?:ql)?(?:\+\w+)?://([^/\s\"'@:]+):([^/\s\"'@]+)@",
    re.IGNORECASE,
)


def _looks_like_real_credential(line: str) -> bool:
    """行内是否含「真口令」形态的连接串（滤掉占位符）。"""
    for m in _CRED_URL.finditer(line):
        pw = m.group(2)
        if len(pw) < 8:
            continue
        if _PLACEHOLDER_PW.match(pw):
            continue
        # 至少含一个大写或数字，才像真实口令（占位词通常全小写字母）
        if not any(c.isupper() or c.isdigit() for c in pw):
            continue
        return True
    return False

# 不扫描的目录
_SKIP_DIRS = {".venv", "venv", "__pycache__", "node_modules", ".pytest_tmp", ".git", "data"}
# 不扫描的文件（本文件要写反面样例作说明）
_SKIP_FILES = {Path(__file__).name}

_NOQA_MARK = "noqa: hardcoded-db"


def _py_files() -> list[Path]:
    if not PKG_ROOT.is_dir():
        pytest.skip(f"data-cleaner 目录不存在: {PKG_ROOT}")
    out: list[Path] = []
    for p in PKG_ROOT.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if p.name in _SKIP_FILES:
            continue
        out.append(p)
    return sorted(out)


def test_scanner_finds_files():
    """确保扫描真的覆盖到了文件（防止路径写错导致空跑通过）。"""
    files = _py_files()
    assert len(files) > 30, f"扫描到的 .py 文件过少（{len(files)}），路径可能不对: {PKG_ROOT}"


def test_no_hardcoded_db_credentials():
    """源码里不得出现带 user:password@ 的 postgres 连接串。"""
    offenders: list[str] = []
    for path in _py_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - 权限/编码异常
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _NOQA_MARK in line:
                continue
            if _looks_like_real_credential(line):
                rel = path.relative_to(PKG_ROOT)
                offenders.append(f"{rel}:{lineno}")

    assert not offenders, (
        "发现硬编码的数据库连接串（含口令），口令绝不进源码/版本库：\n  "
        + "\n  ".join(offenders)
        + "\n\n改法：from app.intel.store import _sync_url → URL = _sync_url()"
    )


def test_scanner_regex_sanity():
    """自检：要能抓真口令、放过占位符，别因为写太松/太严而形同虚设。"""
    # 真口令 → 必须抓
    must_flag = [
        'URL = "postgresql+psycopg2://postgres:abdxJMPj7SWf@127.0.0.1:5432/quant"',
        "DATABASE_URL = 'postgresql://app_user:S3cretPwd99@db:5432/quant'",
    ]
    # 占位符 / 本地默认 / 无口令 → 必须放过
    must_pass = [
        "postgresql+psycopg2://127.0.0.1:5432/quant",  # 无 user:password
        "postgres://host/db",
        'KEY = "not-a-url"',
        '"""postgresql+psycopg2://user:pw@host:port/db → connect kwargs"""',  # docstring 格式说明
        'DATABASE_URL: str = "postgresql+asyncpg://quant_user:password@localhost:5432/quant_db"',
        'URL = "postgresql://admin:***@localhost/db"',
    ]
    for s in must_flag:
        assert _looks_like_real_credential(s), f"应判定为真口令但没有: {s}"
    for s in must_pass:
        assert not _looks_like_real_credential(s), f"不应判定为真口令但判了: {s}"
