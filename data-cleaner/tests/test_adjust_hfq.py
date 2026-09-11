"""R1b / R1c 回归测试（本地数据，无需联网）

覆盖：
R1b
1. adjust 步骤派生 hfq_* 列，且 hfq/adj 为恒定常数（后复权/前复权只差锚点系数）。
2. hfq_close 缺失时降级：不产出 hfq_*，且不影响 adj_*。
3. 公式引擎允许 hfq_* 列（时序因子可显式用后复权）。
4. adj_close 仍是 qfq —— 估值因子（PE = 价/eps）依赖真实价口径，不能被 hfq 替换。

R1c
5. Tushare 拿不到 adj_factor 时**必须报错**，不得静默写入不复权数据。
6. 显式开启 TUSHARE_ALLOW_UNADJUSTED 才放行，且 source 标记为 tushare-unadjusted。
7. pandadata.fetch 默认前复权（R1a 曾因漏传 adjust 写入 585 万行不复权价）。
8. pandadata 对北交所显式报错；backfill 自动路由到 akshare。
"""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.ingestion.pandadata_source import PandadataSource
from app.ingestion.tushare_source import TushareSource
from app.pipeline.adjust import AdjustTransformer


def _make_df(n: int = 30, k: float = 150.0) -> pd.DataFrame:
    """构造带 hfq_close 的单标的 DataFrame，hfq/qfq = k。"""
    close = pd.Series([10.0 + i * 0.1 for i in range(n)])
    return pd.DataFrame(
        {
            "symbol": "TEST.SZ",
            "timestamp": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": [1000.0] * n,
            "source": "csv",
            "freq": "1d",
            "hfq_close": close * k,
        }
    )


# ---------- R1b ----------


def test_adjust_derives_hfq_columns():
    out = AdjustTransformer().transform(_make_df(k=150.0))
    for col in ["hfq_open", "hfq_high", "hfq_low", "hfq_close"]:
        assert col in out.columns, f"缺少 {col}"


def test_hfq_ratio_is_constant():
    """hfq/qfq 必须恒定 —— 两者只差一个锚点系数。"""
    out = AdjustTransformer().transform(_make_df(k=150.0))
    ratio = out["hfq_close"] / out["adj_close"]
    assert ratio.std() < 1e-9
    assert ratio.mean() == pytest.approx(150.0)


def test_k_uses_median_robust_to_single_bad_row():
    """某日 hfq_close 异常时，比值取中位数不应被带偏。"""
    df = _make_df(k=150.0)
    df.loc[5, "hfq_close"] = 999_999.0  # 单点脏值
    out = AdjustTransformer().transform(df)
    ratio = out["hfq_close"] / out["adj_close"]
    assert ratio.mean() == pytest.approx(150.0)


def test_adjust_degrades_without_hfq():
    """raw_bars 尚无 hfq_close 时，不产出 hfq_*，且 adj_* 不受影响。"""
    df = _make_df().drop(columns=["hfq_close"])
    out = AdjustTransformer().transform(df)
    assert "hfq_close" not in out.columns
    assert "hfq_open" not in out.columns
    assert "adj_close" in out.columns


def test_adj_close_stays_qfq():
    """adj_close 必须等于 close（qfq）。

    估值因子 PE = adj_close / eps_ttm 要求真实价口径；若被换成 hfq
    （茅台放大约 150 倍）会得出荒谬的 PE。
    """
    out = AdjustTransformer().transform(_make_df(k=150.0))
    pd.testing.assert_series_equal(
        out["adj_close"], out["close"], check_names=False
    )


def test_formula_allows_hfq_columns():
    from app.factors.formula import _ALLOWED_COLS

    assert {"hfq_open", "hfq_high", "hfq_low", "hfq_close"} <= _ALLOWED_COLS


# ---------- R1c ----------


def _fake_tushare(adj_factor_side_effect):
    """构造一个可调 adj_factor 行为的 tushare mock。"""
    ts = MagicMock()
    ts.set_token.return_value = None
    api = MagicMock()
    if isinstance(adj_factor_side_effect, Exception):
        api.query.side_effect = adj_factor_side_effect
    else:
        api.query.return_value = adj_factor_side_effect
    ts.pro_api.return_value = api

    n = 3
    df = pd.DataFrame(
        {
            "trade_date": ["20240901", "20240902", "20240903"],
            "open": [10.0] * n,
            "high": [10.5] * n,
            "low": [9.5] * n,
            "close": [10.0] * n,
            "vol": [1000.0] * n,
        }
    )
    ts.pro_bar.return_value = df
    return ts


def _run_tushare(ts_mock, allow: bool):
    src = TushareSource()
    with patch.object(src, "_client", return_value=ts_mock), patch(
        "app.ingestion.tushare_source.settings"
    ) as st:
        st.TUSHARE_ALLOW_UNADJUSTED = allow
        return src.fetch("600519.SH", "2024-09-01", "2024-09-03")


def test_tushare_raises_when_adj_factor_unavailable():
    """R1c 核心：拿不到 adj_factor 必须报错，绝不静默写入不复权价。"""
    import pytest as _pt

    ts = _fake_tushare(Exception("抱歉，您访问接口(adj_factor)频率超限"))
    with _pt.raises(Exception) as ei:
        _run_tushare(ts, allow=False)
    assert "拒绝写入不复权" in str(ei.value) or "adj_factor" in str(ei.value)


def test_tushare_marks_source_when_unadjusted_allowed():
    """显式放行时，source 必须可区分，便于下游隔离。"""
    ts = _fake_tushare(Exception("频率超限"))
    out = _run_tushare(ts, allow=True)
    assert len(out) > 0
    assert set(out["source"].unique()) == {"tushare-unadjusted"}


def test_tushare_normal_path_uses_tushare_source():
    """adj_factor 正常时 source 为 tushare。"""
    adj = pd.DataFrame(
        {"trade_date": ["20240901", "20240902", "20240903"],
         "adj_factor": [1.0, 1.2, 1.5]}
    )
    out = _run_tushare(_fake_tushare(adj), allow=False)
    assert set(out["source"].unique()) == {"tushare"}
    assert out["adj_factor"].notna().all()


def test_pandadata_fetch_defaults_to_qfq():
    """R1a 教训：漏传 adjust 会写入不复权价，产生除权假跳空。"""
    src = PandadataSource()
    captured = {}

    def fake(symbols, start, end, adjust=None):
        captured["adjust"] = adjust
        return pd.DataFrame()

    with patch.object(src, "fetch_daily", side_effect=fake):
        src.fetch("600519.SH", "2024-01-01", "2024-01-05")
    assert captured["adjust"] == "pre"


def test_pandadata_rejects_bj_explicitly():
    """北交所不支持时必须显式报错，由 backfill 路由到 akshare。"""
    import pytest as _pt

    src = PandadataSource()
    with _pt.raises(RuntimeError) as ei:
        src.fetch("920808.BJ", "2024-01-01", "2024-01-05")
    assert "akshare" in str(ei.value)


def test_backfill_routes_bj_to_akshare():
    """source=pandadata 时，BJ 标的应自动改用 akshare 源。"""
    from app.tasks import backfill as bf

    seen = {}

    def fake_get_source(name):
        seen["name"] = name
        return MagicMock()

    with patch.object(bf, "get_source", side_effect=fake_get_source), patch.object(
        bf.repository, "get_latest_date", return_value="2026-09-04"
    ):
        bf.backfill_symbol("pandadata", "920808.BJ", today="2026-09-08")
    assert seen["name"] == "akshare"
