import sys
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()
import akshare as ak

df = ak.stock_financial_analysis_indicator(symbol="600519", start_year="2024")
print("列:", list(df.columns))
print("行数:", len(df))
for c in df.columns:
    if "收益" in str(c) or "利润" in str(c) or "eps" in str(c).lower() or "股" in str(c):
        print(c, "->", df[c].iloc[0])
