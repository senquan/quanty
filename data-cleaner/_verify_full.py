"""回填后全量复核：从 raw_bars 抽样 ~200 标的跑清洗+因子，报告各因子覆盖率与合流列填充率。"""
import os
import sys
import random

import pandas as pd
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.getcwd())

from sqlalchemy import create_engine, text

from app.core.config import settings
from app.factors.registry import compute_factor
from app.pipeline.runner import CleaningPipeline
from app.storage.raw_store import repository
from app.tasks.factor_build import _merge_fundamental

u = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
e = create_engine(u)
with e.connect() as c:
    syms = [r[0] for r in c.execute(text("SELECT DISTINCT symbol FROM factor.raw_bars")).fetchall()]
random.seed(0)
sample = random.sample(syms, min(200, len(syms)))
print(f"抽样 {len(sample)} / {len(syms)} 标的")

raw = repository.load_all(symbols=sample)
raw["timestamp"] = pd.to_datetime(raw["timestamp"])

pipeline = CleaningPipeline()
parts = []
for sym, g in raw.groupby("symbol", sort=False):
    cleaned, _ = pipeline.run(g.sort_values("timestamp").reset_index(drop=True))
    parts.append(cleaned)
panel = pd.concat(parts, ignore_index=True)
panel["timestamp"] = pd.to_datetime(panel["timestamp"])
panel = _merge_fundamental(panel)

n = len(panel)
print(f"panel 行数 {n}")
print("合流列填充率:")
for col in ["amount", "eps_ttm", "industry", "list_date", "dividend_ttm", "roe", "debt_ratio"]:
    if col in panel.columns:
        print(f"  {col}: {panel[col].notna().mean()*100:.1f}%")
    else:
        print(f"  {col}: (缺失列)")

print("因子覆盖率:")
for code in ["VAL_PE_TTM", "VOL_STD_20_ANN", "LIQ_AMOUNT_20", "VAL_DIV_YIELD",
             "FND_ROE", "FND_DEBT_RATIO", "GRO_EPS_GROWTH_YOY"]:
    try:
        s = compute_factor(code, panel)
        print(f"  {code}: {s.notna().mean()*100:.1f}% ({int(s.notna().sum())}/{n})")
    except Exception as ex:
        print(f"  {code}: 计算失败 {ex}")
