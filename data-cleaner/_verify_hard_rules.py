"""临时验证：第一层 hard_rules 纯函数逻辑（合成数据，无需 DB）。

验证点：
1. normalize_hard_rules：无效 op / 未注册因子 / 动态阈值降级
2. apply_hard_rules：固定阈值 & quantile 动态阈值 & 三类角色全通过判定
3. scores_at 的 restrict 过滤
"""
import pandas as pd

from app.strategy import engine as E


def _frame(date, data):
    """data: {symbol: value} -> DataFrame(index=[date], columns=symbol)"""
    return pd.DataFrame([data], index=[date])


def test_normalize():
    print("== normalize_hard_rules ==")
    # 有效 + 无效 op + 未注册因子 + 动态阈值
    raw = [
        {"factor": "LIQ_AMOUNT_20", "op": ">=", "value": 50_000_000, "role": "liquidity"},
        {"factor": "VAL_PE_TTM", "op": "BADOP", "value": 25, "role": "core"},  # 无效 op
        {"factor": "NOT_A_FACTOR", "op": "<=", "value": 1, "role": "core"},     # 未注册
        {"factor": "FND_ROE", "op": ">=", "value": 12, "role": "core",
         "dynamic": {"mode": "quantile", "quantile": 0.7}},                     # 动态
        {"factor": "GRO_EPS_GROWTH_YOY", "op": ">=", "value": 15, "role": "weird"},  # 角色降级 core
    ]
    norm = E.normalize_hard_rules(raw)
    print("保留规则数:", len(norm), "(应为 3：5 条中去掉无效op与未注册因子)")
    assert len(norm) == 3, norm
    dyn = [r for r in norm if r["dynamic"]]
    assert dyn and dyn[0]["factor"] == "FND_ROE" and dyn[0]["dynamic"]["mode"] == "quantile"
    weird = [r for r in norm if r["factor"] == "GRO_EPS_GROWTH_YOY"][0]
    assert weird["role"] == "core"
    print("  OK: 无效项被剔除，动态阈值与角色降级正确")


def test_fixed_threshold():
    print("== apply_hard_rules 固定阈值 ==")
    as_of = "2026-09-01"
    frames = {
        "VAL_PE_TTM": _frame(as_of, {"A": 10, "B": 30, "C": 20, "D": 15}),
        "FND_ROE": _frame(as_of, {"A": 20, "B": 5, "C": 15, "D": 12}),
    }
    rules = [
        {"factor": "VAL_PE_TTM", "op": "<=", "value": 25, "role": "core"},
        {"factor": "FND_ROE", "op": ">=", "value": 12, "role": "core"},
    ]
    qualified, info = E.apply_hard_rules(as_of, frames, rules)
    # A: pe10<=25 & roe20>=12 -> pass; B: roe5<12 fail; C: pass; D: pass
    print("  qualified:", sorted(qualified))
    assert qualified == {"A", "C", "D"}, qualified
    print("  OK: 固定阈值全通过判定正确")


def test_quantile_dynamic():
    print("== apply_hard_rules quantile 动态阈值 ==")
    as_of = "2026-09-01"
    # PE 分布：10,20,30,40,50 -> quantile 0.7 ≈ 第 70 分位
    frames = {
        "VAL_PE_TTM": _frame(as_of, {"A": 10, "B": 20, "C": 30, "D": 40, "E": 50}),
    }
    rules = [
        {"factor": "VAL_PE_TTM", "op": "<=", "value": 0, "role": "core",
         "dynamic": {"mode": "quantile", "quantile": 0.7}},
    ]
    qualified, info = E.apply_hard_rules(as_of, frames, rules)
    # quantile 0.7 of [10,20,30,40,50] = 38 (线性插值) -> <=38 通过: A,B,C
    print("  threshold:", info["rules"][0]["threshold"], "qualified:", sorted(qualified))
    assert info["rules"][0]["threshold"] is not None
    assert qualified == {"A", "B", "C"}, qualified
    print("  OK: quantile 动态阈值正确")


def test_factor_missing():
    print("== apply_hard_rules 因子当日无数据 ==")
    as_of = "2026-09-01"
    frames = {"VAL_PE_TTM": _frame("2026-08-31", {"A": 10})}
    rules = [{"factor": "VAL_PE_TTM", "op": "<=", "value": 25, "role": "core"}]
    qualified, info = E.apply_hard_rules(as_of, frames, rules)
    print("  qualified:", qualified, "available:", info["rules"][0]["available"])
    assert qualified == set()
    assert info["rules"][0]["available"] is False
    print("  OK: 因子无数据 -> 合格池为空")


def test_restrict_in_scores_at():
    print("== scores_at restrict 过滤 ==")
    as_of = "2026-09-01"
    frames = {
        "VAL_PE_TTM": _frame(as_of, {"A": 10, "B": 30, "C": 20}),
    }
    ind_map = {}
    weights = {"VAL_PE_TTM": 1.0}
    # 不限制：全部 3 只都进入得分（z-score 后）
    s_all, _ = E.scores_at(as_of, frames, ind_map, weights, "none")
    # 限制到 {A, C}
    s_res, _ = E.scores_at(as_of, frames, ind_map, weights, "none", restrict={"A", "C"})
    print("  all:", sorted(s_all.index), "restricted:", sorted(s_res.index))
    assert set(s_all.index) == {"A", "B", "C"}
    assert set(s_res.index) == {"A", "C"}
    print("  OK: restrict 正确缩小打分标的")


if __name__ == "__main__":
    test_normalize()
    test_fixed_threshold()
    test_quantile_dynamic()
    test_factor_missing()
    test_restrict_in_scores_at()
    print("\nALL PASS ✅")
