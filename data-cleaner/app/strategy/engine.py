"""因子选股策略引擎

职责（贴近 data-cleaner 的因子/行情数据，避免经 HTTP 搬大数据）：
- 加载因子横截面、标准化、行业中性化
- 权重解析：manual 直接取配置 / auto_ir 按历史 |IR| 归一化（防前视）
- 计算综合得分 Score = Σ w_i · Z_i，选前 N 构建持仓
- 回测：T 日收盘算分 → T+1 开盘（近似用次日收盘）等权买入 → 持有至下个调仓 → 出净值与指标

所有"未来信息"严格禁止：回测中 auto_ir 的 IC 窗口只取 < 信号日 的截面；
实时权重直接复用 factor.metrics 的最新 IR（流水线每日已算）。
"""
from datetime import datetime, timedelta
import math
import operator

import numpy as np
import pandas as pd

from app.core.config import settings
from app.core.logging import get_logger
from app.industry import store as industry_store
from app.storage import db
from app.storage import fundamental_store
from app.storage.parquet_store import parquet_store
from app.storage.raw_store import repository

logger = get_logger(__name__)

MIN_CROSS_SECTION = 50

# 涨跌停判定浮点容差（价格近似比较）
TOL = 1e-6

# daily_basic.total_mv 单位（tushare 为千元）；min_cap 配置以"亿元"表达，
# 故阈值 = min_cap * 1e5（千元）。若接入 akshare 路径且单位为元需同步调整。
MV_UNIT_PER_YI = 1e5


def _clean(v):
    """递归把 NaN / ±Inf 转成 None，确保结果可安全序列化为 JSON / jsonb。"""
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


# --------------------------------------------------------------------------- #
# 因子加载
# --------------------------------------------------------------------------- #
def _category_of(code: str) -> str | None:
    try:
        from app.factors.registry import get_factor

        return get_factor(code).get_metadata().get("category")
    except Exception:  # noqa: BLE001
        return None


def load_factor_frames(
    factor_codes: list[str], start: str | None = None, end: str | None = None
) -> dict[str, pd.DataFrame]:
    """返回 {code: DataFrame(index=date:str, columns=symbol)}。

    只读取该因子所属类别的 parquet，按日期归并；缺失的日期/标的记为 NaN。
    """
    by_cat: dict[str, list[str]] = {}
    for code in factor_codes:
        cat = _category_of(code)
        if not cat:
            logger.warning("无法识别因子类别，跳过: %s", code)
            continue
        by_cat.setdefault(cat, []).append(code)

    frames: dict[str, dict[str, pd.Series]] = {c: {} for c in factor_codes}
    for cat, codes in by_cat.items():
        d = settings.factor_data_path / cat
        if not d.exists():
            continue
        for f in sorted(d.glob("*.parquet")):
            dt = f.stem
            if start and dt < start:
                continue
            if end and dt > end:
                continue
            df = pd.read_parquet(f)
            for code in codes:
                if code in df.columns:
                    frames.setdefault(code, {})[dt] = df[code].rename(dt)

    out: dict[str, pd.DataFrame] = {}
    for code, dmap in frames.items():
        if dmap:
            out[code] = pd.DataFrame(dmap).T  # index=date, columns=symbol
    return out


def load_price_panel(start: str | None = None, end: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """读取收盘价面板。

    返回 (prices, suspended_bar)：
    - prices: 前向填充后的收盘价面板（index=date, columns=symbol），缺价补 0；
      用于盯市，保证净值曲线 / 指标不因缺失而 NaN。
    - suspended_bar: 与 prices 同形的布尔面板，True 表示该日该标"无 bar"，
      即停牌 / 未上市（与 trading_status.suspended 合并构成停牌掩码）。
    """
    raw = repository.load_all(start=start, end=end)
    if raw.empty:
        return pd.DataFrame(), pd.DataFrame()
    raw["timestamp"] = pd.to_datetime(raw["timestamp"])
    raw["date"] = raw["timestamp"].dt.strftime("%Y-%m-%d")
    panel = raw.pivot(index="date", columns="symbol", values="close")
    # 无 bar 即视为停牌/未上市（align 前向填充价但标记停牌，避免"剔除即失踪"）
    suspended_bar = panel.isna()
    # 前向填充缺失收盘价（某标的当日无数据则沿用上一可得价），再补 0，
    # 避免回测盯市时出现 NaN 导致净值曲线 / 指标异常。
    prices = panel.sort_index().ffill().fillna(0.0)
    suspended_bar = suspended_bar.reindex(index=prices.index, columns=prices.columns).fillna(False)
    return prices, suspended_bar


def _panel(
    long_df: pd.DataFrame,
    value: str,
    dates: list[str],
    symbols: list[str],
    agg: str = "first",
    fill_value=None,
) -> pd.DataFrame:
    """把 trading_status / daily_basic 长表按 (trade_date, symbol) 透视成宽面板并
    对齐到给定 dates×symbols 网格。缺失填 fill_value（默认保留 NaN）。"""
    if long_df is None or long_df.empty:
        p = pd.DataFrame(index=dates, columns=symbols)
    else:
        p = long_df.pivot_table(index="trade_date", columns="symbol", values=value, aggfunc=agg)
        p = p.reindex(index=dates, columns=symbols)
    if fill_value is not None:
        p = p.fillna(fill_value)
    return p


def factor_availability() -> dict[str, bool]:
    """返回 {code: 该因子在其类别最新一天 parquet 中是否存在列}（近似是否有因子值）。

    仅读取每个类别目录的最新一个 parquet 文件的列名，开销约 7 次文件读，
    用于前端创建策略时把"暂无因子值"的因子置灰。
    """
    from app.factors.registry import list_factors

    metas = list_factors()
    by_cat: dict[str, list[str]] = {}
    for m in metas:
        by_cat.setdefault(m.get("category"), []).append(m["code"])
    result: dict[str, bool] = {}
    for cat, codes in by_cat.items():
        present: dict[str, int] = {}
        if cat:
            d = settings.factor_data_path / cat
            files = sorted(d.glob("*.parquet")) if d.exists() else []
            if files:
                try:
                    df = pd.read_parquet(files[-1])
                    present = {
                        c: int(df[c].notna().sum()) for c in df.columns if c in df
                    }
                except Exception:  # noqa: BLE001
                    present = {}
        for c in codes:
            # 至少 100 只标的含有效值才视为"可用"，避免把全 NaN 因子暴露给用户
            result[c] = present.get(c, 0) > 100
    return result


# --------------------------------------------------------------------------- #
# 标准化 / 行业中性化
# --------------------------------------------------------------------------- #
def standardize(series: pd.Series) -> pd.Series:
    """截面 z-score；标准差过小返回 0。"""
    mu, sd = series.mean(), series.std()
    if sd is None or sd <= 1e-12:
        return pd.Series(0.0, index=series.index)
    return (series - mu) / sd


def industry_neutralize(series: pd.Series, ind_map: dict) -> pd.Series:
    """行业内 z-score；行业内样本 < 5 时退化为全市场 z-score。

    ind_map: {symbol: industry}，未命中归为 '未知'。
    """
    ind = series.index.to_series().map(lambda s: ind_map.get(s, "未知"))
    grouped = series.groupby(ind)
    out = pd.Series(index=series.index, dtype=float)
    global_z = standardize(series)
    for _name, idx in grouped.groups.items():
        sub = series.loc[idx]
        if len(sub) < 5:
            out.loc[idx] = global_z.loc[idx]
        else:
            out.loc[idx] = standardize(sub)
    return out.fillna(0.0)


# --------------------------------------------------------------------------- #
# 权重解析
# --------------------------------------------------------------------------- #
def _normalize_manual(weights: dict, factor_codes: list[str]) -> dict:
    w = {c: float(weights.get(c, 0.0) or 0.0) for c in factor_codes}
    total = sum(w.values())
    if total <= 1e-12:
        return {c: 1.0 / len(factor_codes) for c in factor_codes}
    return {c: v / total for c, v in w.items()}


async def resolve_weights_auto_realtime(factor_codes: list[str]) -> dict:
    """实时权重：取 factor.metrics 最新 IR（流水线每日已算），按 |IR| 归一化。"""
    metrics = await db.list_latest_factor_metrics()
    irs = {
        c: abs(float((metrics.get(c) or {}).get("ir") or 0.0)) for c in factor_codes
    }
    total = sum(irs.values())
    if total <= 1e-12:
        return {c: 1.0 / len(factor_codes) for c in factor_codes}
    return {c: v / total for c, v in irs.items()}


def _spearman_ic(fvals: pd.Series, rvals: pd.Series) -> float | None:
    common = fvals.dropna().index.intersection(rvals.dropna().index)
    if len(common) < MIN_CROSS_SECTION:
        return None
    f = fvals.reindex(common)
    r = rvals.reindex(common)
    try:
        return float(f.corr(r, method="spearman"))
    except Exception:  # noqa: BLE001
        return None


def resolve_weights_auto_backtest(
    factor_codes: list[str],
    frames: dict[str, pd.DataFrame],
    fwd: pd.DataFrame,
    as_of: str,
    lookback: int = 60,
) -> dict:
    """回测权重：在信号日 as_of 之前 lookback 个交易日内逐日算 IC → IR → |IR| 归一。

    严格防前视：只用严格早于 as_of 的截面（IC 由 因子值(d) 与 次日收益(d) 构成）。
    """
    irs: dict[str, float] = {}
    for code in factor_codes:
        frame = frames.get(code)
        if frame is None or code not in fwd.columns:
            irs[code] = 0.0
            continue
        dates = sorted(d for d in frame.index if d < as_of)[-lookback:]
        ics = []
        for d in dates:
            if d not in fwd.index:
                continue
            ic = _spearman_ic(frame.loc[d], fwd.loc[d])
            if ic is not None:
                ics.append(ic)
        if len(ics) < 20:
            irs[code] = 0.0
            continue
        mean = float(np.mean(ics))
        std = float(np.std(ics))
        irs[code] = abs(mean / std) if std > 1e-12 else 0.0

    total = sum(irs.values())
    if total <= 1e-12:
        return {c: 1.0 / len(factor_codes) for c in factor_codes}
    return {c: v / total for c, v in irs.items()}


# --------------------------------------------------------------------------- #
# 得分与选股
# --------------------------------------------------------------------------- #
def scores_at(
    date: str,
    frames: dict[str, pd.DataFrame],
    ind_map: dict,
    weights: dict,
    neutralize: str,
    universe: object = None,
    custom_codes: list[str] | None = None,
    restrict: set[str] | None = None,
) -> tuple[pd.Series, dict]:
    """计算某日各股票综合得分（index=symbol）。

    返回 (composite_scores, z_map)，其中 z_map[symbol] = {factor_code: 中性化后 z 值}，
    供详情页逐标的展示各因子 z 值与行业。
    无因子值/权重为 0 的因子跳过；universe 为板块 key 列表（空=全市场），
    与 custom_codes（自选股）取并集过滤标的。
    """
    acc: pd.Series | None = None
    used = False
    z_map: dict[str, dict[str, float]] = {}
    for code, w in weights.items():
        if not w or code not in frames or date not in frames[code].index:
            continue
        used = True
        s = frames[code].loc[date].dropna()
        s = (
            industry_neutralize(s, ind_map)
            if neutralize == "industry"
            else standardize(s)
        )
        for sym, val in s.items():
            z_map.setdefault(sym, {})[code] = round(float(val), 4)
        acc = s * w if acc is None else acc.add(s * w, fill_value=0.0)
    if not used or acc is None:
        return pd.Series(dtype=float), {}
    acc = acc.dropna()
    if _normalize_universe(universe):
        mask = acc.index.map(lambda s: in_universe(s, universe, custom_codes))
        acc = acc[mask]
    # 第一层硬阈值筛选：仅保留合格池标的（hard_rules 已在调用方算好）
    if restrict is not None:
        acc = acc[acc.index.isin(restrict)]
        z_map = {s: v for s, v in z_map.items() if s in restrict}
    return acc, z_map


def normalize_filters(config: dict) -> dict:
    """统一 filters 默认值与类型，集中化配置校验，避免调用方散落 .get 默认值。

    接受的键：exclude_st, min_list_days, exclude_suspended, exclude_limit_up,
    exclude_limit_down, min_cap（亿元）。
    """
    f = config.get("filters") or {} if isinstance(config, dict) else {}

    def _bool(v, d):
        return d if v is None else bool(v)

    def _int(v, d):
        try:
            return int(v)
        except (TypeError, ValueError):
            return d

    min_cap = f.get("min_cap")
    if min_cap in (None, ""):
        min_cap = None
    else:
        try:
            min_cap = float(min_cap)
        except (TypeError, ValueError):
            min_cap = None
    return {
        "exclude_st": _bool(f.get("exclude_st"), True),
        "min_list_days": _int(f.get("min_list_days"), 60),
        "exclude_suspended": _bool(f.get("exclude_suspended"), True),
        "exclude_limit_up": _bool(f.get("exclude_limit_up"), True),
        "exclude_limit_down": _bool(f.get("exclude_limit_down"), False),
        "min_cap": min_cap,
        "hard_rules": normalize_hard_rules(f.get("hard_rules")),
    }


# --------------------------------------------------------------------------- #
# 第一层：硬性阈值筛选（hard_rules，配置驱动）
# --------------------------------------------------------------------------- #
# op 与 role 的白名单（与 docs/plans/2026-09-02.three-layer-strategy-design.md §2 对齐）
_HARD_OPS = {
    "<=": operator.le,
    ">=": operator.ge,
    "<": operator.lt,
    ">": operator.gt,
    "==": operator.eq,
    "!=": operator.ne,
}
_HARD_ROLES = {"core", "liquidity", "risk"}


def normalize_hard_rules(rules) -> list[dict]:
    """统一 hard_rules 默认值与类型，集中配置校验；无效项以 warning 忽略。

    接受的规则字段（见设计文档 §2.2）：
    - factor: 因子 code（必须已在 registry 注册）
    - op:     <= >= < > == !=
    - value:  固定阈值（与 dynamic 二选一）
    - role:   core | liquidity | risk（默认 core）
    - dynamic: 动态阈值 {mode:"quantile", quantile:0.0~1.0}（一期仅 quantile）
    """
    if not rules:
        return []
    out: list[dict] = []
    for r in rules:
        if not isinstance(r, dict):
            continue
        factor = r.get("factor")
        op = r.get("op")
        if not factor or op not in _HARD_OPS:
            logger.warning("忽略无效 hard_rule（缺 factor 或 op 不合法）: %s", r)
            continue
        try:
            from app.factors.registry import get_factor

            get_factor(factor)
        except Exception:  # noqa: BLE001
            logger.warning("hard_rule 因子未注册，已忽略: %s", factor)
            continue
        role = r.get("role") or "core"
        if role not in _HARD_ROLES:
            role = "core"
        dyn = r.get("dynamic")
        if dyn is not None:
            # 一期仅支持 quantile；其它 mode 忽略降级为固定阈值
            if not isinstance(dyn, dict) or dyn.get("mode") != "quantile":
                dyn = None
        rule: dict = {"factor": factor, "op": op, "role": role, "dynamic": dyn}
        rule["value"] = r.get("value")
        out.append(rule)
    return out


def _resolve_threshold(cross: pd.Series, rule: dict) -> float | None:
    """解析单条规则的阈值：dynamic(quantile) 优先，否则用固定 value。"""
    dyn = rule.get("dynamic")
    if dyn:
        q = dyn.get("quantile")
        try:
            q = float(q)
        except (TypeError, ValueError):
            return None
        if not (0.0 < q < 1.0):
            return None
        if cross.empty:
            return None
        return float(cross.quantile(q))
    val = rule.get("value")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def apply_hard_rules(
    as_of: str,
    frames: dict[str, pd.DataFrame],
    rules: list[dict],
    restrict: set[str] | None = None,
) -> tuple[set[str], dict]:
    """第一层：对候选池逐标的判定，全部 hard_rules 通过才进入合格池。

    返回 (qualified, info)：
    - qualified: 通过全部规则的标的集合（restrict 给定时先缩到 restrict）
    - info: {rules:[{factor,op,role,threshold,available,passed,total}],
             detail:{symbol:{rule_index:bool}}} 供诊断/前端展示

    设计文档 §2.1：core / liquidity / risk 三类角色**全部参与**通过性判定，
    role 仅决定该规则是否计入「达标程度评分」（层三溢出逻辑用，本期未启用）。
    固定值与动态阈值二选一（normalize_hard_rules 已校验）。
    """
    if not rules:
        # 无硬规则：合格池 = 候选集（restrict 或全市场有因子值标的）
        if restrict is not None:
            return set(restrict), {"rules": [], "detail": {}}
        candidates = set()
        for f in frames.values():
            if as_of in f.index:
                candidates |= set(f.loc[as_of].dropna().index)
        return candidates, {"rules": [], "detail": {}}

    # 候选池：restrict 优先；否则取所有 hard_rule 因子在该日有值标的的并集
    if restrict is not None:
        candidates = set(restrict)
    else:
        candidates = set()
        for r in rules:
            f = frames.get(r["factor"])
            if f is not None and as_of in f.index:
                candidates |= set(f.loc[as_of].dropna().index)

    qualified = set(candidates)
    detail: dict[str, dict[int, bool]] = {s: {} for s in candidates}
    rule_summary: list[dict] = []
    for i, r in enumerate(rules):
        factor, op, role = r["factor"], r["op"], r["role"]
        frame = frames.get(factor)
        if frame is None or as_of not in frame.index:
            # 因子当日无数据 → 无人能通过该规则
            qualified = set()
            rule_summary.append(
                {"factor": factor, "role": role, "op": op,
                 "threshold": None, "available": False,
                 "passed": 0, "total": len(candidates)}
            )
            continue
        cross = frame.loc[as_of].dropna()
        thr = _resolve_threshold(cross, r)
        if thr is None:
            qualified = set()
            rule_summary.append(
                {"factor": factor, "role": role, "op": op,
                 "threshold": None, "available": True,
                 "passed": 0, "total": len(candidates)}
            )
            continue
        mask = _HARD_OPS[op](cross, thr)
        passing = set(mask[mask].index)
        qualified &= passing
        for s in candidates:
            detail[s][i] = s in passing
        rule_summary.append(
            {"factor": factor, "role": role, "op": op,
             "threshold": round(thr, 6), "available": True,
             "passed": len(passing), "total": len(candidates)}
        )
    return qualified, {"rules": rule_summary, "detail": detail}


def apply_filters(
    symbols: list[str],
    meta_map: dict,
    as_of: str,
    prices: pd.Series | None = None,
    suspended: dict | None = None,
    limit_up: dict | None = None,
    limit_down: dict | None = None,
    total_mv: dict | None = None,
    exclude_st: bool = True,
    min_list_days: int = 60,
    exclude_suspended: bool = True,
    exclude_limit_up: bool = True,
    exclude_limit_down: bool = False,
    min_cap: float | None = None,
) -> list[str]:
    """可配过滤：在候选标的（已按得分选出）上做交易性剔除。

    - exclude_suspended: 停牌(suspended=True) 剔除
    - exclude_limit_up:  买入侧，close >= limit_up（涨停）剔除，买不进
    - exclude_limit_down: 卖出侧语义，close <= limit_down（跌停）剔除（避免接飞刀）
    - min_cap:           总市值下限（亿元），total_mv < min_cap*1e5(千元) 剔除
    prices/suspended/limit_up/limit_down/total_mv 均为 as_of 当日、{symbol: 值} 的映射；
    缺失则该项不生效（兼容 trading_status / daily_basic 尚未刷新的标的）。
    """
    out = []
    cutoff = None
    if min_list_days and min_list_days > 0:
        try:
            cutoff = (
                datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=min_list_days)
            ).date()
        except Exception:  # noqa: BLE001
            cutoff = None
    cap_thresh = (min_cap * MV_UNIT_PER_YI) if min_cap is not None else None
    for s in symbols:
        m = meta_map.get(s, {})
        name = (m.get("name") or "")
        if exclude_st and "ST" in name.upper():
            continue
        if cutoff is not None:
            ld = m.get("list_date")
            if ld and ld > cutoff:
                continue
        if exclude_suspended and suspended and suspended.get(s):
            continue
        if exclude_limit_up and limit_up is not None and prices is not None:
            lu = limit_up.get(s)
            p = prices.get(s)
            if lu is not None and p is not None and not pd.isna(lu) and not pd.isna(p) \
                    and float(p) >= float(lu) - TOL:
                continue
        if exclude_limit_down and limit_down is not None and prices is not None:
            ld = limit_down.get(s)
            p = prices.get(s)
            if ld is not None and p is not None and not pd.isna(ld) and not pd.isna(p) \
                    and float(p) <= float(ld) + TOL:
                continue
        if cap_thresh is not None and total_mv is not None:
            mv = total_mv.get(s)
            if mv is None or pd.isna(mv) or float(mv) < cap_thresh:
                continue
        out.append(s)
    return out


def select_top_n(scores: pd.Series, top_n: int) -> list[str]:
    if scores.empty:
        return []
    return scores.sort_values(ascending=False).head(top_n).index.tolist()


# --------------------------------------------------------------------------- #
# 标的股票池（板块过滤）
# --------------------------------------------------------------------------- #
# 板块 -> 代码前 3 位前缀（忽略 .SH/.SZ 后缀，取数字部分判断）
UNIVERSE_BOARDS: dict[str, list[str]] = {
    "main": ["600", "601", "603", "605", "000", "001", "002", "003"],  # 沪深主板
    "cyb": ["300", "301"],  # 创业板
    "kcb": ["688", "689"],  # 科创板
    "bj": ["8", "920"],  # 北交所（8 字头新三板平移 + 920 注册制新股）
}
UNIVERSE_LABELS: dict[str, str] = {
    "main": "沪深主板",
    "cyb": "创业板",
    "kcb": "科创板",
}


def _symbol_digits(symbol: str) -> str:
    """提取代码数字部分（600519.SH -> 600519），用于板块前缀/自选股匹配。"""
    return "".join(ch for ch in symbol if ch.isdigit())


def _normalize_universe(universe: object) -> list[str]:
    """标的股票池统一为板块 key 列表；空列表表示全市场。

    兼容旧配置：字符串 'all'/'custom' -> 空列表（自选股由 custom_codes 决定），
    其它字符串（单板块）-> 单元素列表。
    """
    if not universe:
        return []
    if isinstance(universe, str):
        return [] if universe in ("all", "custom") else [universe]
    if isinstance(universe, (list, tuple, set)):
        out: list[str] = []
        for u in universe:
            if not isinstance(u, str):
                out.append(str(u))
                continue
            key = u.strip().lower()
            # 板块 key（main/cyb/kcb/bj）原样保留——原先对列表元素一律套
            # _symbol_digits()[:3]，会把 'main'/'cyb' 这类无数字的 key 抹成 ''，
            # 导致多板块配置（如 ['main','cyb']）过滤时无标的命中、得分全空。
            # 非板块 key 才按代码取前 3 位前缀。
            out.append(key if key in UNIVERSE_BOARDS else _symbol_digits(u)[:3])
        return out
    return []


def in_universe(
    symbol: str, universe: object, custom_codes: list[str] | None = None
) -> bool:
    """判断 symbol 是否入选标的股票池。

    - universe: 板块 key 列表（main/cyb/kcb），空/None 表示全市场。
    - custom_codes: 自选股代码列表，按 6 位代码与板块取并集。
    """
    digits = _symbol_digits(symbol)
    # 自选股（跨板块，并集）
    if custom_codes:
        codes = {_symbol_digits(c)[:6] for c in custom_codes}
        if digits[:6] in codes:
            return True
    # 板块过滤：未选板块 => 全市场
    boards = _normalize_universe(universe)
    if not boards:
        return True
    for b in boards:
        for p in UNIVERSE_BOARDS.get(b, []):
            if digits[: len(p)] == p:
                return True
    return False


# --------------------------------------------------------------------------- #
# 回测
# --------------------------------------------------------------------------- #
def _schedule_dates(all_dates: list[str], rebalance: dict) -> list[str]:
    freq = (rebalance or {}).get("freq", "weekly")
    if freq == "monthly":
        step = 21
    elif freq == "every_n_days":
        step = max(1, int((rebalance or {}).get("every_n_days", 5) or 5))
    else:  # weekly
        step = 5
    return all_dates[::step]


def run_backtest(
    config: dict,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """运行因子选股回测。

    返回 {metrics, nav[], rebalances[], warnings[]}。
    nav 项: {date, value}；rebalances 项: {date, tradeDate, weights, holdings[{symbol,score,weight}]}
    """
    factor_codes = config.get("factor_codes") or []
    if len(factor_codes) < 1:
        return {"error": "至少选择一个因子"}
    top_n = int(config.get("top_n", 30))
    neutralize = config.get("neutralize", "industry")
    weight_mode = config.get("weight_mode", "auto_ir")
    manual_weights = config.get("weights") or {}
    initial_capital = float(config.get("initial_capital", 1_000_000))
    rebalance = config.get("rebalance") or {"freq": "weekly"}
    filters = normalize_filters(config)
    hard_rules = filters.get("hard_rules") or []
    lookback = int(config.get("lookback_days", 60))
    universe = _normalize_universe(config.get("universe"))
    custom_codes = config.get("custom_codes") or []

    # 第一层所需因子 = 打分因子 ∪ hard_rules 因子
    all_codes = sorted(set(factor_codes) | {r["factor"] for r in hard_rules})
    frames = load_factor_frames(all_codes, start, end)
    missing = [c for c in factor_codes if c not in frames]
    warnings: list[str] = []
    if missing:
        warnings.append(f"以下因子无因子值已忽略: {', '.join(missing)}")
    factor_codes = [c for c in factor_codes if c in frames]
    if not factor_codes:
        return {"error": "所选因子均无因子值，无法回测"}

    # 价格面板与停牌掩码（缺 bar = 停牌）
    prices, suspended_bar = load_price_panel(start, end)
    if prices.empty:
        return {"error": "行情为空，无法回测"}

    fwd = prices.shift(-1).div(prices) - 1  # 次日收益（近似，与流水线同源：前复权）

    ind_map = industry_store._sync_industry_map()
    meta_map = industry_store._sync_meta_map()

    # 交易日历以「有行情的日期」为准（只有这些日期才能按收盘价成交），
    # 避免出现因子帧有、行情无的日期导致 prices.loc[d] 报 KeyError。
    all_dates = sorted(prices.index)
    all_dates = [d for d in all_dates if (not start or d >= start) and (not end or d <= end)]
    if len(all_dates) < 2:
        return {"error": "可用交易日不足"}

    schedule = _schedule_dates(all_dates, rebalance)

    # 交易状态 / 市值面板（供过滤与回测成交判定）
    ts = fundamental_store.load_trading_status(start, end)
    suspended_ts = _panel(ts, "suspended", all_dates, list(prices.columns), agg="max", fill_value=False).astype(bool)
    limit_up_ts = _panel(ts, "limit_up", all_dates, list(prices.columns))
    limit_down_ts = _panel(ts, "limit_down", all_dates, list(prices.columns))
    db = fundamental_store.load_daily_basic(start, end)
    mv_ts = _panel(db, "total_mv", all_dates, list(prices.columns))
    # 停牌掩码：缺 bar（停牌/未上市）与 trading_status.suspended 取并集
    suspended_all = (
        suspended_bar.reindex(index=prices.index, columns=prices.columns).fillna(False)
        | suspended_ts.fillna(False)
    ).fillna(False)

    # 预计算每次调仓的目标持仓
    rebalances: list[dict] = []
    trade_map: dict[str, tuple[list[str], dict]] = {}
    for T in schedule:
        if weight_mode == "manual":
            weights = _normalize_manual(manual_weights, factor_codes)
        else:
            weights = resolve_weights_auto_backtest(
                factor_codes, frames, fwd, T, lookback
            )
        # 第一层：硬性阈值筛选 → 合格池（restrict 仅保留合格池中标的参与打分）
        qualified, _ = apply_hard_rules(T, frames, hard_rules)
        scores, z_map = scores_at(
            T, frames, ind_map, weights, neutralize, universe, custom_codes,
            restrict=qualified,
        )
        if scores.empty:
            warnings.append(f"{T} 无有效得分（合格池 {len(qualified)} 只），跳过")
            continue
        picked = select_top_n(scores, top_n)
        susp_row = suspended_all.loc[T] if T in suspended_all.index else None
        up_row = limit_up_ts.loc[T] if T in limit_up_ts.index else None
        dn_row = limit_down_ts.loc[T] if T in limit_down_ts.index else None
        mv_row = mv_ts.loc[T] if T in mv_ts.index else None
        picked = apply_filters(
            picked, meta_map, T,
            prices=prices.loc[T],
            suspended=(susp_row.to_dict() if susp_row is not None else {}),
            limit_up=(up_row.to_dict() if up_row is not None else {}),
            limit_down=(dn_row.to_dict() if dn_row is not None else {}),
            total_mv=(mv_row.to_dict() if mv_row is not None else {}),
            exclude_st=filters["exclude_st"],
            min_list_days=filters["min_list_days"],
            exclude_suspended=filters["exclude_suspended"],
            exclude_limit_up=filters["exclude_limit_up"],
            exclude_limit_down=filters["exclude_limit_down"],
            min_cap=filters["min_cap"],
        )
        if not picked:
            warnings.append(f"{T} 过滤后无可用标的，跳过")
            continue
        # 交易日在信号日的下一个可用日
        td = _next_date(all_dates, T)
        if not td:
            continue
        hold_w = round(1.0 / len(picked), 4) if picked else 0.0
        rebalances.append(
            {
                "date": T,
                "tradeDate": td,
                "weights": {c: round(w, 4) for c, w in weights.items()},
                "holdings": [
                    {
                        "symbol": s,
                        "score": round(float(scores[s]), 4),
                        "weight": hold_w,
                        "industry": ind_map.get(s, "未知"),
                        "z_scores": z_map.get(s, {}),
                    }
                    for s in picked
                ],
            }
        )
        trade_map[td] = (picked, weights)

    if not trade_map:
        return {"error": "无有效调仓窗口", "warnings": warnings}

    trade_dates = sorted(trade_map)
    first_trade = trade_dates[0]
    last_trade = trade_dates[-1]

    # 逐日盯市
    cash = initial_capital
    shares: dict[str, int] = {}
    nav: list[dict] = []
    traded_value_total = 0.0
    portfolio_value_at_trade = []

    for d in all_dates:
        if d < first_trade:
            continue
        row = prices.loc[d]
        if d in trade_map:
            target, _weights = trade_map[d]
        up = limit_up_ts.loc[d] if d in limit_up_ts.index else None
        dn = limit_down_ts.loc[d] if d in limit_down_ts.index else None
        susp = suspended_all.loc[d] if d in suspended_all.index else None
        up_d = up.to_dict() if up is not None else {}
        dn_d = dn.to_dict() if dn is not None else {}
        susp_d = susp.to_dict() if susp is not None else {}
        # 卖出：跌停 / 停牌不可卖，保留持仓（提升成交真实度）
        kept = {}
        for s, q in shares.items():
            p = row.get(s)
            if p and p > 0:
                ld = dn_d.get(s)
                if (susp_d.get(s)) or (
                    ld is not None and not pd.isna(ld) and float(p) <= float(ld) + TOL
                ):
                    kept[s] = q
                    continue
                cash += q * p
        shares = kept
        # 买入目标：涨停 / 停牌不可买，跳过（用当日收盘价近似开盘成交）
        n = len(target)
        per = cash / n if n else 0.0
        for s in target:
            if susp_d.get(s):
                continue
            p = row.get(s)
            lu = up_d.get(s)
            if p and p > 0 and not (
                lu is not None and not pd.isna(lu) and float(p) >= float(lu) - TOL
            ):
                q = int(per / p / 100) * 100
                if q > 0:
                    shares[s] = shares.get(s, 0) + q
                    cash -= q * p * 1.0003  # 手续费万三
            traded_value_total += cash + sum(
                shares[s] * row.get(s, 0) for s in shares
            )
            portfolio_value_at_trade.append(
                cash + sum(shares[s] * row.get(s, 0) for s in shares)
            )
        value = cash + sum(shares.get(s, 0) * (row.get(s) or 0) for s in shares)
        nav.append({"date": d, "value": round(float(value), 2)})
        if d >= last_trade:
            break

    metrics = _metrics_from_nav(
        nav, initial_capital, len(rebalances), traded_value_total
    )
    metrics["rebalances"] = len(rebalances)
    return _clean(
        {
            "metrics": metrics,
            "nav": nav,
            "rebalances": rebalances,
            "warnings": warnings,
        }
    )


def _next_date(all_dates: list[str], d: str) -> str | None:
    for x in all_dates:
        if x > d:
            return x
    return None


def _metrics_from_nav(
    nav: list[dict], initial: float, n_reb: int, traded_value_total: float
) -> dict:
    if len(nav) < 2:
        return {
            "totalReturn": 0.0, "annualReturn": 0.0, "sharpe": 0.0,
            "maxDrawdown": 0.0, "winRate": 0.0, "turnover": 0.0,
            "finalCapital": initial, "days": len(nav),
        }
    vals = np.array([x["value"] for x in nav], dtype=float)
    rets = np.diff(vals) / vals[:-1]
    total_return = float(vals[-1] / vals[0] - 1)
    n_days = len(nav)
    annual_return = float((vals[-1] / vals[0]) ** (252 / max(n_days - 1, 1)) - 1)
    sd = float(np.std(rets))
    sharpe = float(np.mean(rets) / sd * (252**0.5)) if sd > 1e-12 else 0.0
    # 最大回撤
    peak = np.maximum.accumulate(vals)
    mdd = float(((vals - peak) / peak).min())
    # 换手率：平均每次调仓单边换手 = 期间成交金额/调仓次数 / 组合均值
    avg_pv = float(np.mean(vals))
    turnover = (
        float(traded_value_total / n_reb / avg_pv) if n_reb and avg_pv else 0.0
    )
    return {
        "totalReturn": round(total_return, 4),
        "annualReturn": round(annual_return, 4),
        "sharpe": round(sharpe, 4),
        "maxDrawdown": round(mdd, 4),
        "winRate": 0.0,  # 调仓期胜率需更细拆分，留待后续
        "turnover": round(turnover, 4),
        "finalCapital": round(float(vals[-1]), 2),
        "days": n_days,
    }


# --------------------------------------------------------------------------- #
# 实时目标持仓（供模拟盘调仓使用）
# --------------------------------------------------------------------------- #
def compute_target(config: dict, as_of: str | None = None) -> dict:
    """计算某日（默认最新）的目标持仓。

    返回 {date, weights, scores:{symbol:score}, holdings:[{symbol,score,weight}]}
    """
    factor_codes = config.get("factor_codes") or []
    top_n = int(config.get("top_n", 30))
    neutralize = config.get("neutralize", "industry")
    weight_mode = config.get("weight_mode", "auto_ir")
    manual_weights = config.get("weights") or {}
    filters = normalize_filters(config)
    hard_rules = filters.get("hard_rules") or []
    lookback = int(config.get("lookback_days", 60))
    universe = _normalize_universe(config.get("universe"))
    custom_codes = config.get("custom_codes") or []

    # 第一层所需因子 = 打分因子 ∪ hard_rules 因子（避免硬阈值因子未加载）
    all_codes = sorted(set(factor_codes) | {r["factor"] for r in hard_rules})
    frames = load_factor_frames(all_codes)
    factor_codes = [c for c in factor_codes if c in frames]
    if not factor_codes:
        return {"error": "无可用因子值"}

    ind_map = industry_store._sync_industry_map()
    meta_map = industry_store._sync_meta_map()

    if as_of is None:
        # 取所有因子帧共有的最新日期
        as_of = max(
            (d for f in frames.values() for d in f.index), default=None
        )
    if as_of is None:
        return {"error": "无因子日期"}
    if as_of not in set().union(*[set(f.index) for f in frames.values()]):
        as_of = max(
            d for d in set().union(*[set(f.index) for f in frames.values()]) if d <= as_of
        )

    if weight_mode == "manual":
        weights = _normalize_manual(manual_weights, factor_codes)
    else:
        weights = industry_store._run(resolve_weights_auto_realtime, factor_codes)

    # 第一层：硬性阈值筛选 → 合格池（restrict 仅保留合格池中标的参与打分）
    qualified, hard_info = apply_hard_rules(as_of, frames, hard_rules)
    hard_info["qualified"] = len(qualified)
    scores, z_map = scores_at(
        as_of, frames, ind_map, weights, neutralize, universe, custom_codes,
        restrict=qualified,
    )
    if scores.empty:
        return {
            "error": f"{as_of} 无有效得分（合格池 {len(qualified)} 只）",
            "date": as_of,
            "hard_filter": _clean(hard_info),
        }

    # as_of 当日交易状态 / 市值（供过滤）
    ts = fundamental_store.load_trading_status(start=as_of, end=as_of)
    suspended = dict(zip(ts["symbol"], ts["suspended"])) if not ts.empty else {}
    limit_up = dict(zip(ts["symbol"], ts["limit_up"])) if not ts.empty else {}
    limit_down = dict(zip(ts["symbol"], ts["limit_down"])) if not ts.empty else {}
    db = fundamental_store.load_daily_basic(start=as_of, end=as_of)
    total_mv = dict(zip(db["symbol"], db["total_mv"])) if not db.empty else {}
    prices_asof, suspended_bar_asof = load_price_panel(start=as_of, end=as_of)
    prices_row = (
        prices_asof.loc[as_of]
        if (not prices_asof.empty and as_of in prices_asof.index)
        else pd.Series(dtype=float)
    )
    # 缺 bar 标的视为停牌（与 trading_status.suspended 合并）
    if not suspended_bar_asof.empty:
        for sym, is_susp in suspended_bar_asof.iloc[0].to_dict().items():
            if is_susp and sym not in suspended:
                suspended[sym] = True

    picked = apply_filters(
        select_top_n(scores, top_n),
        meta_map, as_of,
        prices=prices_row,
        suspended=suspended,
        limit_up=limit_up,
        limit_down=limit_down,
        total_mv=total_mv,
        exclude_st=filters["exclude_st"],
        min_list_days=filters["min_list_days"],
        exclude_suspended=filters["exclude_suspended"],
        exclude_limit_up=filters["exclude_limit_up"],
        exclude_limit_down=filters["exclude_limit_down"],
        min_cap=filters["min_cap"],
    )
    hold_w = round(1.0 / len(picked), 4) if picked else 0.0
    return _clean(
        {
            "date": as_of,
            "weights": {c: round(w, 4) for c, w in weights.items()},
            "scores": {s: round(float(scores[s]), 4) for s in picked},
            "hard_filter": hard_info,
            "holdings": [
                {
                    "symbol": s,
                    "score": round(float(scores[s]), 4),
                    "weight": hold_w,
                    "industry": ind_map.get(s, "未知"),
                    "z_scores": z_map.get(s, {}),
                }
                for s in picked
            ],
        }
    )
