"""迁移静态守卫：禁止「建了又被删」的死索引，并锁定 author_profiles 的唯一键口径。

背景（2026-09-16 实际事故）：
    app/storage/db.py 的 apply_migrations() 是**每次启动全量重放**所有
    migrations/*.sql —— 没有 applied 记录表、没有校验和、没有版本号比对。
    这意味着迁移文件里**不能留历史包袱**：任何「建了又被后续迁移 drop」的语句，
    每次重启都会白跑一遍，且可能在数据已演进到新形态后直接失败。

    013 建了 3 列唯一索引 idx_intel_profiles_key_ver，018 又把它 drop 换成含
    as_of 的 4 列唯一索引。D-9 上线后，同一 (key, type, ver) **合法地**存在多个
    as_of 版本行（实测 14 个 key × 2 个 as_of），于是 013 重放时重建那个已被
    废弃的 3 列唯一索引 → UniqueViolationError → 迁移中断、dc 起不来。

规则：
    R1 死索引：某索引名有 CREATE 语句，但按文件顺序模拟到最后的最终态是
       「不存在」（= 被后来的 DROP 删掉）⇒ 违规。
       DROP 之后再 CREATE（合法重建，如 018 的 drop-then-create）不违规。
    R2 唯一键口径：intel.author_profiles 最终生效的唯一索引必须包含 as_of
       （否则 D-9 版本化语义被静默破坏 —— 同 key 的多 as_of 会互相顶掉）。
"""
from __future__ import annotations

import re
from pathlib import Path

MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"

# CREATE [UNIQUE] INDEX [IF NOT EXISTS] <name>
_CREATE = re.compile(
    r"create\s+(unique\s+)?index\s+(?:if\s+not\s+exists\s+)?([\w.$]+)",
    re.IGNORECASE,
)
# CREATE UNIQUE INDEX ... ON <table>(<cols>) —— 跨行，取列组合
_CREATE_UNIQUE_COLS = re.compile(
    r"create\s+unique\s+index\s+(?:if\s+not\s+exists\s+)?[\w.$]+\s+on\s+([\w.]+)\s*\(([^)]*)\)",
    re.IGNORECASE | re.DOTALL,
)
_DROP = re.compile(r"drop\s+index\s+(?:if\s+exists\s+)?([\w.$]+)", re.IGNORECASE)


def _bare(name: str) -> str:
    """去掉 schema 前缀并统一小写（CREATE 不带前缀、DROP 常带 intel. 前缀）。"""
    return name.split(".")[-1].lower()


def _strip_comments(sql: str) -> str:
    """剥掉 `--` 注释行 —— 注释里的示例不该被当成真语句。"""
    return "\n".join(
        ln for ln in sql.splitlines() if not ln.strip().startswith("--")
    )


def _scan_texts(files: list[tuple[str, str]]):
    """扫描 (文件名, SQL 文本) 序列。

    Returns:
        (events, unique_defs)
        events: [(seq, kind, name)]，kind ∈ {"create","drop"}，按文件顺序 + 文件内位置排序
        unique_defs: {index_name: (table, [cols])}
    """
    events: list[tuple[int, str, str]] = []
    unique_defs: dict[str, tuple[str, list[str]]] = {}
    seq = 0
    for _fname, raw in files:
        sql = _strip_comments(raw)
        local: list[tuple[int, str, str]] = []
        for m in _CREATE.finditer(sql):
            local.append((m.start(), "create", _bare(m.group(2))))
        for m in _DROP.finditer(sql):
            local.append((m.start(), "drop", _bare(m.group(1))))
        for _pos, kind, name in sorted(local):
            seq += 1
            events.append((seq, kind, name))
        for m in _CREATE_UNIQUE_COLS.finditer(sql):
            # 索引名要单独取（本正则第 1 组是表名），故再扫一次 CREATE
            head = sql[max(0, m.start()):m.end()]
            nm = _CREATE.search(head)
            if nm:
                cols = [c.strip().lower() for c in m.group(2).split(",") if c.strip()]
                unique_defs[_bare(nm.group(2))] = (_bare(m.group(1)), cols)
    return events, unique_defs


def _scan():
    files = sorted(MIG_DIR.glob("*.sql"))
    assert files, f"未找到迁移文件: {MIG_DIR}"
    return _scan_texts([(f.name, f.read_text(encoding="utf-8")) for f in files])


def _alive(events) -> set[str]:
    """按顺序模拟 create/drop，返回最终仍存在的索引名。"""
    alive: set[str] = set()
    for _seq, kind, name in events:
        if kind == "create":
            alive.add(name)
        else:
            alive.discard(name)
    return alive


def _dead_indexes(events) -> list[str]:
    """被创建、但最终态不存在的索引名（= 建了又被删）。"""
    created = {n for _, k, n in events if k == "create"}
    return sorted(created - _alive(events))


# ---------------------------------------------------------------- R1 死索引

def test_no_dead_index_in_migrations():
    events, _ = _scan()
    dead = _dead_indexes(events)
    assert not dead, (
        "以下索引在迁移里被 CREATE、又被后续迁移 DROP —— apply_migrations() 每次启动"
        "全量重放，这些 CREATE 会白跑一遍；当数据已演进到新形态时可能直接撞唯一冲突"
        f"（2026-09-16 事故成因）。请删掉过期的 CREATE，只保留最终态写法：{dead}"
    )


# ---------------------------------------------------------------- R2 画像唯一键

def test_author_profiles_unique_index_includes_as_of():
    events, unique_defs = _scan()
    alive = _alive(events)
    hits = [
        name
        for name in sorted(alive)
        if name in unique_defs and unique_defs[name][0] == "author_profiles"
    ]
    assert hits, (
        "intel.author_profiles 没有任何最终生效的唯一索引 —— D-9 的 as_of 版本化"
        "会失去唯一性约束（profile.py 的 ON CONFLICT 将无法命中）"
    )
    for name in hits:
        cols = unique_defs[name][1]
        assert "as_of" in cols, (
            f"唯一索引 {name} 的列 {cols} 不含 as_of —— 同一 profile_key 的多个"
            "as_of 版本会互相顶掉，D-9 版本化失效"
        )


# ---------------------------------------------------------------- 扫描器自检

def test_scanner_catches_dead_index():
    """自检：把 2026-09-16 的事故形态喂给扫描器，必须报出来。

    不碰真实迁移文件 —— 只喂文本，所以这个用例本身不会改任何东西。
    """
    only_013 = [
        ("013_x.sql", "create unique index if not exists idx_old on t(a, b);"),
    ]
    with_018 = [
        only_013[0],
        (
            "018_x.sql",
            "-- 换个唯一键\ndrop index if exists t.idx_old;\n"
            "create unique index if not exists idx_new on t(a, b, c);",
        ),
    ]
    # 只有 013 时：索引存在，不算死
    assert _dead_indexes(_scan_texts(only_013)[0]) == []
    # 加上 018 后：idx_old 建了又被删 → 必须被抓出来
    ev, defs = _scan_texts(with_018)
    assert _dead_indexes(ev) == ["idx_old"], (
        f"扫描器没抓出死索引 —— 正则或事件排序失效：events={ev}, defs={defs}"
    )
    # 合法重建（drop 之后 create）不算死索引
    rebuilt = [
        ("010_a.sql", "create index if not exists idx_k on t(a);"),
        ("020_b.sql", "drop index if exists t.idx_k;\ncreate index if not exists idx_k on t(a, b);"),
    ]
    assert _dead_indexes(_scan_texts(rebuilt)[0]) == []


def test_scanner_sees_real_events():
    """自检：真实迁移目录里必须能扫到 create/drop，且识别出 018 的列组合。"""
    events, unique_defs = _scan()
    kinds = {k for _, k, _ in events}
    assert "create" in kinds, "未扫到任何 CREATE INDEX —— 正则失效"
    assert "drop" in kinds, "未扫到任何 DROP INDEX —— 正则失效"
    key = "idx_intel_profiles_key_ver_asof"
    assert key in unique_defs, f"未识别到 {key} —— 跨行列组合正则失效"
    assert "as_of" in unique_defs[key][1], unique_defs[key]
    assert unique_defs[key][0] == "author_profiles", unique_defs[key]
