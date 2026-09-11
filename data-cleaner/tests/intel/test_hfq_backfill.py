"""D-2 回填 hfq_close 的回归测试

覆盖三件事：
1. k 常数法正确：``hfq_close = close * k``，k = 该标的已有 ``hfq_close/close`` 的中位数
2. 幂等：重复运行不重复写入；**已填的行不会被覆盖**
3. **两条写入路径都会自动补**（2026-09-10 补漏）：
   - ``bulk_upsert``（历史补录用，execute_values）
   - ``upsert``（**每日增量实际走这条**，backfill.py 调 repository.upsert 逐行调存储过程）

第 3 条是被真实事故教出来的：最初只给 ``bulk_upsert`` 加了钩子，结果重启 dc 后
每日增量写入的新行照样缺 hfq（40→80 只标的持续涨），因为增量根本不走 bulk 路径。
两条路径必须都守住，否则「每过一天多缺一天」会复发。

用真实库 + 临时 symbol（测完必删），因为 UPDATE 用了 PG 的 ``percentile_cont``
和 ``ANY(text[])``，SQLite 替身测不出真实语义。
"""
import uuid

import pytest
from sqlalchemy import create_engine, text

from app.intel import store
from app.tasks.hfq_refresh import FREQ_DAILY, backfill, count_missing

# ⚠️ 不要在这里硬编码 DB URL：曾因此把生产库口令写进源码（入库前已改）。
# 统一从 app 配置取（settings.DATABASE_URL，读 .env），driver 降级交给 store._sync_url()。
DB_URL = store._sync_url()


@pytest.fixture
def engine():
    return create_engine(DB_URL)


@pytest.fixture
def tmp_symbol(engine):
    """造一只临时标的：4 行有 hfq（供算 k）+ 2 行 hfq 为空（待补）"""
    sym = f"ZZ{uuid.uuid4().hex[:6].upper()}.SH"
    with engine.begin() as c:
        # 前 4 天：hfq = close * 3（k=3）
        for i, (close, hfq) in enumerate(
            [(10.0, 30.0), (11.0, 33.0), (12.0, 36.0), (13.0, 39.0)]
        ):
            c.execute(
                text("""
                    INSERT INTO factor.raw_bars
                        (symbol, timestamp, open, high, low, close, volume,
                         source, freq, hfq_close)
                    VALUES (:s, DATE '2026-01-01' + :i, :c, :c, :c, :c, 100,
                            'pytest', :fq, :h)
                    ON CONFLICT (symbol, timestamp, freq) DO NOTHING
                """),
                {"s": sym, "i": i, "c": close, "fq": FREQ_DAILY, "h": hfq},
            )
        # 后 2 天：hfq 缺失（模拟增量入库只写 qfq 的 close）
        for i, close in enumerate([14.0, 15.0], start=4):
            c.execute(
                text("""
                    INSERT INTO factor.raw_bars
                        (symbol, timestamp, open, high, low, close, volume,
                         source, freq, hfq_close)
                    VALUES (:s, DATE '2026-01-01' + :i, :c, :c, :c, :c, 100,
                            'pytest', :fq, NULL)
                    ON CONFLICT (symbol, timestamp, freq) DO NOTHING
                """),
                {"s": sym, "i": i, "c": close, "fq": FREQ_DAILY},
            )
    yield sym
    with engine.begin() as c:
        c.execute(text("DELETE FROM factor.raw_bars WHERE symbol = :s"), {"s": sym})


def _rows(engine, sym):
    """按时间升序返回 [(date, close, hfq_close), ...]，hfq_close 可能为 None"""
    with engine.connect() as c:
        return [
            (r[0], float(r[1]), None if r[2] is None else float(r[2]))
            for r in c.execute(
                text("""
                    SELECT timestamp::date, close, hfq_close FROM factor.raw_bars
                    WHERE symbol = :s AND freq = :fq ORDER BY timestamp
                """),
                {"s": sym, "fq": FREQ_DAILY},
            ).all()
        ]


def test_backfill_fills_missing_using_k(tmp_symbol, engine):
    """k = 3（前 4 天 hfq/close）⇒ 缺失两天应补成 42 / 45"""
    res = backfill(engine, symbols=[tmp_symbol])

    assert res["updated"] == 2, res
    rows = _rows(engine, tmp_symbol)
    # 前 4 天已有值，后 2 天为待补（索引 4、5）
    assert rows[4][2] == pytest.approx(42.0)  # close 14 → hfq = 14 * 3
    assert rows[5][2] == pytest.approx(45.0)  # close 15 → hfq = 15 * 3


def test_backfill_is_idempotent(tmp_symbol, engine):
    """第二次运行不应再写入任何行"""
    backfill(engine, symbols=[tmp_symbol])
    second = backfill(engine, symbols=[tmp_symbol])
    assert second["updated"] == 0


def test_backfill_never_overwrites_existing(tmp_symbol, engine):
    """已有 hfq 的行必须原样保留（不允许被 k 重算覆盖）"""
    before = _rows(engine, tmp_symbol)
    backfill(engine, symbols=[tmp_symbol])
    after = _rows(engine, tmp_symbol)
    for i in range(4):  # 前 4 行原本就有 hfq
        assert after[i] == before[i], f"第 {i} 行被改动了"


def test_symbol_without_any_hfq_is_orphan(engine):
    """从未有过 hfq 的标的算不出 k → 进 orphans，不会被瞎填"""
    sym = f"ZZ{uuid.uuid4().hex[:6].upper()}.SH"
    try:
        with engine.begin() as c:
            c.execute(
                text("""
                    INSERT INTO factor.raw_bars
                        (symbol, timestamp, open, high, low, close, volume,
                         source, freq, hfq_close)
                    VALUES (:s, DATE '2026-02-02', 5, 5, 5, 5, 100, 'pytest', :fq, NULL)
                    ON CONFLICT (symbol, timestamp, freq) DO NOTHING
                """),
                {"s": sym, "fq": FREQ_DAILY},
            )
        backfill(engine, symbols=[sym])
        rows = _rows(engine, sym)
        assert len(rows) == 1
        assert rows[0][2] is None, "无历史 hfq 的标的不该被填值"
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM factor.raw_bars WHERE symbol = :s"), {"s": sym})


def test_count_missing_returns_shape(engine):
    m = count_missing(engine)
    assert {"rows", "symbols", "date_min", "date_max"} <= set(m)
    assert isinstance(m["rows"], int)


# ---------------------------------------------------------------------------
# 两条写入路径都要自动补 hfq（2026-09-10 补漏，见文件头 docstring 第 3 条）
# ---------------------------------------------------------------------------

def _next_day_df(sym: str, day_offset: int, close: float) -> "object":
    """构造一行「增量源只给 qfq close、hfq 为 NULL」的 DataFrame"""
    import pandas as pd

    return pd.DataFrame(
        [
            {
                "symbol": sym,
                "timestamp": pd.Timestamp("2026-01-01")
                + pd.Timedelta(days=day_offset),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 100,
                "source": "pytest",
                "freq": FREQ_DAILY,
                "hfq_close": None,  # 增量源（pandadata）无复权因子，恒为 NULL
            }
        ]
    )


def test_upsert_row_path_fills_hfq(engine, tmp_symbol):
    """``upsert()`` 逐行路径（**每日增量走这条**）写入后必须自动补 hfq"""
    from app.storage.raw_store import RawBarRepository

    repo = RawBarRepository()
    if repo._engine is None:  # parquet 降级环境，跳过（不伪造通过）
        pytest.skip("无 PG 连接，走的是 parquet 降级路径")
    # 复用 fixture 的连接池：全量跑时若每个用例都新建 Engine，连接池会被打满，
    # upsert 拿不到连接就静默回退 parquet，测出来是「没补 hfq」的假失败。
    repo._engine = engine

    repo.upsert(_next_day_df(tmp_symbol, 6, 16.0))

    with engine.connect() as c:
        got = c.execute(
            text("""
                SELECT hfq_close FROM factor.raw_bars
                WHERE symbol = :s AND freq = :fq
                  AND timestamp = DATE '2026-01-01' + 6
            """),
            {"s": tmp_symbol, "fq": FREQ_DAILY},
        ).scalar()
    # 先确认行真的进了 PG（写入失败会静默回退 parquet，那样查出来是 None）,
    # 别让「连不上库」伪装成「没补 hfq」两种完全不同的故障。
    exists = engine.connect().execute(
        text("""
            SELECT count(*) FROM factor.raw_bars
            WHERE symbol = :s AND freq = :fq AND timestamp = DATE '2026-01-01' + 6
        """),
        {"s": tmp_symbol, "fq": FREQ_DAILY},
    ).scalar()
    assert exists == 1, "行没写进 PG（upsert 回退 parquet 了？）—— 检查连接而非回填逻辑"
    # k = 3（见 tmp_symbol fixture：hfq = close * 3）
    assert got is not None, "upsert 路径没有补 hfq —— 每日增量会持续缺值"
    assert got == pytest.approx(48.0), f"补的值不对：期望 16*3=48，实际 {got}"


def test_bulk_upsert_path_fills_hfq(engine, tmp_symbol):
    """``bulk_upsert()`` 批量路径（历史补录用）写入后同样要自动补"""
    from app.storage.raw_store import RawBarRepository

    repo = RawBarRepository()
    if repo._engine is None:
        pytest.skip("无 PG 连接，走的是 parquet 降级路径")
    repo._engine = engine

    repo.bulk_upsert(_next_day_df(tmp_symbol, 7, 17.0))

    with engine.connect() as c:
        got = c.execute(
            text("""
                SELECT hfq_close FROM factor.raw_bars
                WHERE symbol = :s AND freq = :fq
                  AND timestamp = DATE '2026-01-01' + 7
            """),
            {"s": tmp_symbol, "fq": FREQ_DAILY},
        ).scalar()
    assert got is not None, "bulk_upsert 路径没有补 hfq"
    assert got == pytest.approx(51.0), f"补的值不对：期望 17*3=51，实际 {got}"


def test_upsert_does_not_wipe_existing_hfq(engine, tmp_symbol):
    """重拉同一交易日（hfq 传 NULL）不能把已回填的值冲掉 —— COALESCE 语义"""
    from app.storage.raw_store import RawBarRepository

    repo = RawBarRepository()
    if repo._engine is None:
        pytest.skip("无 PG 连接，走的是 parquet 降级路径")
    repo._engine = engine

    # 重拉第 0 天（原 hfq=30），hfq 传 NULL
    repo.upsert(_next_day_df(tmp_symbol, 0, 10.0))

    with engine.connect() as c:
        got = c.execute(
            text("""
                SELECT hfq_close FROM factor.raw_bars
                WHERE symbol = :s AND freq = :fq
                  AND timestamp = DATE '2026-01-01'
            """),
            {"s": tmp_symbol, "fq": FREQ_DAILY},
        ).scalar()
    assert got == pytest.approx(30.0), f"已回填的值被冲掉了：{got}"
