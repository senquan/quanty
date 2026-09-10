"""P1-1: 灌 intel.symbol_alias 种子数据

三路来源（全部幂等，ON CONFLICT (alias, symbol) DO NOTHING）：
  1. factor.stock_info / factor.industries 的代码 → 6 位裸代码别名（alias_type='code'）
     如 600519.SH → alias "600519"，文本里的 6 位代码可回查标准代码
  2. factor.industries 的 name → 简称别名（alias_type='name'，内嵌空格归一化）
     如 "五 粮 液" → "五粮液" → 000858.SZ
  3. 脚本内置人工俗称表（alias_type='nickname'）：茅台/宁王/迪王等
     来源标注 source='manual'，与 seed 区分

用法：
  python scripts/seed_symbol_alias.py          # dry-run，打印统计
  python scripts/seed_symbol_alias.py --apply  # 实际写库
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.intel.store import get_engine

# 人工俗称 → 标准代码（与 factor.stock_info.symbol 对齐）
# 原则：只收高频、无歧义的俗称；两字俗称极易误伤（如"中兴"撞普通词），宁缺毋滥
NICKNAMES: dict[str, str] = {
    "茅台": "600519.SH",
    "五粮液": "000858.SZ",
    "宁王": "300750.SZ",
    "宁德时代": "300750.SZ",
    "迪王": "002594.SZ",
    "比亚迪": "002594.SZ",
    "中远海控": "601919.SH",
    "隆基绿能": "601012.SH",
    "汇川技术": "300124.SZ",
    "立讯精密": "002475.SZ",
    "药明康德": "603259.SH",
    "长江电力": "600900.SH",
    "中国平安": "601318.SH",
    "招商银行": "600036.SH",
    "宁德": "300750.SZ",
    "光伏茅": "601012.SH",
    "券茅": "300059.SZ",
    "东方财富": "300059.SZ",
    "海天味业": "603288.SH",
    "爱尔眼科": "300015.SZ",
    "片仔癀": "600436.SH",
    "北方稀土": "600111.SH",
    "紫金矿业": "601899.SH",
    "万华化学": "600309.SH",
}


def _norm_name(name: str) -> str:
    """名称归一化：去所有空白（'五 粮 液' → '五粮液'）"""
    return "".join(name.split())


def collect_aliases() -> list[dict]:
    """收集全部别名 → [{alias, symbol, alias_type, source}]"""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(alias: str, symbol: str, alias_type: str, source: str) -> None:
        alias = alias.strip()
        if not alias or len(alias) < 2:  # 单字符别名必误伤
            return
        key = (alias, symbol)
        if key in seen:
            return
        seen.add(key)
        out.append({"alias": alias, "symbol": symbol,
                    "alias_type": alias_type, "source": source})

    eng = get_engine()
    with eng.connect() as c:
        # 1) 代码别名 + 2) 名称别名（industries 名称最全，5558 标的）
        rows = c.execute(text(
            "SELECT DISTINCT symbol FROM factor.industries "
            "UNION SELECT DISTINCT symbol FROM factor.stock_info"
        )).scalars().all()
        for sym in rows:
            sym = (sym or "").strip()
            code = sym.split(".")[0]
            if len(code) == 6 and code.isdigit():
                add(code, sym, "code", "seed")

        name_rows = c.execute(text(
            "SELECT symbol, name FROM factor.industries WHERE name IS NOT NULL"
        )).all()
        for sym, name in name_rows:
            add(_norm_name(name), sym, "name", "seed")

    # 3) 人工俗称
    for nick, sym in NICKNAMES.items():
        add(nick, sym, "nickname", "manual")

    return out


def apply(aliases: list[dict]) -> int:
    with get_engine().begin() as c:
        total = 0
        for a in aliases:
            r = c.execute(text(
                """
                INSERT INTO intel.symbol_alias (alias, symbol, alias_type, source)
                VALUES (:alias, :symbol, :alias_type, :source)
                ON CONFLICT (alias, symbol) DO NOTHING
                """
            ), a)
            total += r.rowcount
    return total


def main() -> None:
    is_apply = "--apply" in sys.argv
    aliases = collect_aliases()
    by_type: dict[str, int] = {}
    for a in aliases:
        by_type[a["alias_type"]] = by_type.get(a["alias_type"], 0) + 1

    print(f"收集别名 {len(aliases)} 条：{by_type}")
    print("样例：")
    for a in aliases[:3] + [x for x in aliases if x["alias_type"] == "nickname"][:5]:
        print(f"  {a['alias']:<8} -> {a['symbol']:<10} ({a['alias_type']}/{a['source']})")

    if not is_apply:
        print("\ndry-run 完成（--apply 实际写库）")
        return

    n = apply(aliases)
    print(f"\n完成：新增 {n} 条（其余已存在，幂等跳过）")


if __name__ == "__main__":
    main()
