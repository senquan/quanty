import sys
sys.path.insert(0, ".")
from app.ingestion import tushare_source as tsmod

src = tsmod.TushareSource()
ts = src._client()
pro = ts.pro_api()
td = "20260903"

# 1) 原始价整批
try:
    df = pro.daily(trade_date=td)
    print("RAW daily shape=", None if df is None else df.shape)
    if df is not None and not df.empty:
        print("cols=", list(df.columns))
        print(df.head(2).to_string())
except Exception as e:
    print("RAW_FAIL", repr(e))

# 2) 前复权整批（部分版本支持 adj=）
for adj in ("qfq", "hfq"):
    try:
        df2 = pro.daily(trade_date=td, adj=adj)
        print(f"{adj} daily shape=", None if df2 is None else df2.shape)
    except Exception as e:
        print(f"{adj}_FAIL", repr(e))

# 3) 当日 adj_factor 整批（用于手动 qfq）
try:
    af = pro.adj_factor(trade_date=td)
    print("adj_factor shape=", None if af is None else af.shape, "cols=", None if af is None else list(af.columns))
except Exception as e:
    print("ADJFAIL", repr(e))
