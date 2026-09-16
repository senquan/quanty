"""迁移重放自检：改过 migrations/*.sql 之后，跑本脚本确认 dc 还能起来。

为什么需要它
------------
apply_migrations()（app/storage/db.py）是**每次启动全量重放**所有 migrations/*.sql
—— 没有 applied 记录表、没有校验和、没有版本比对。所以：

    「迁移文件语法正确」 ≠ 「迁移能重放」

下面两类问题只会在重放时暴露，本地静态检查看不出来：
  1. 建了又被后续迁移 drop 的语句（每次重启都白建一次）；
  2. 依赖「当前数据形态」的唯一索引 —— 一旦数据已演进到新形态就撞唯一冲突。

2026-09-16 事故即第 2 类：013 重建已被 018 废弃的 3 列唯一索引
idx_intel_profiles_key_ver，而 D-9 上线后同一 (key,type,ver) 合法地存在多个
as_of 版本行 → 重放时 UniqueViolationError → 迁移中断、dc 起不来。

用法
----
    cd data-cleaner && .venv/Scripts/python.exe _verify_migrations_replay.py

退出码 0 = 重放通过（可以重启 dc）；非 0 = 会崩，先修迁移。
副作用：会真的执行一次迁移（全部 DDL 幂等，不改业务数据）。
"""
import asyncio
import sys

sys.path.insert(0, ".")


async def _replay() -> int:
    from app.storage.db import apply_migrations

    files = await apply_migrations()
    print(f"[OK] apply_migrations 重放通过（{len(files)} 个迁移文件）")
    for f in files:
        print(f"     - {f.name}")
    return 0


def _check_profile_index() -> int:
    """复查 D-9 口径：author_profiles 最终生效的唯一索引必须含 as_of。"""
    from sqlalchemy import text

    from app.intel.store import get_engine

    bad = 0
    with get_engine().connect() as c:
        rows = c.execute(
            text(
                """
                SELECT indexname, indexdef FROM pg_indexes
                WHERE schemaname = 'intel' AND tablename = 'author_profiles'
                ORDER BY indexname
                """
            )
        ).mappings().all()
        uniq = [r for r in rows if "UNIQUE" in (r["indexdef"] or "").upper()
                and "pkey" not in r["indexname"]]
        print("== intel.author_profiles 唯一索引（非主键）==")
        if not uniq:
            print("  [!] 没有任何唯一索引 —— profile.py 的 ON CONFLICT 会失败")
            bad += 1
        for r in uniq:
            ok = "as_of" in r["indexdef"]
            print(f"  {'[OK]' if ok else '[!]'} {r['indexname']}")
            if not ok:
                bad += 1
        # 已被废弃的旧索引不该还在
        stale = [r for r in rows if r["indexname"] == "idx_intel_profiles_key_ver"]
        if stale:
            print("  [!] 残留已废弃索引 idx_intel_profiles_key_ver（013 又把 CREATE 加回来了？）")
            bad += 1

        n = c.execute(text("SELECT count(*) FROM intel.author_profiles")).scalar()
        vs = c.execute(
            text("SELECT count(DISTINCT as_of) FROM intel.author_profiles")
        ).scalar()
        print(f"== 画像行数 = {n}，as_of 版本数 = {vs} ==")
    return bad


if __name__ == "__main__":
    try:
        rc = asyncio.run(_replay())
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] apply_migrations 崩了 —— dc 起不来：{type(exc).__name__}: {exc}")
        sys.exit(1)
    rc += _check_profile_index()
    if rc:
        print(f"[FAIL] 有 {rc} 项口径异常")
        sys.exit(1)
    print("[OK] 全部检查通过")
