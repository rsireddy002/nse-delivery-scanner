"""
precompute_oi.py - Run LOCALLY after 7 PM IST (same routine as precompute.py),
then commit + push. Same reasoning as the delivery scanner: Streamlit Cloud's
US datacenter IPs get 403'd by NSE, so fetch on your PC and commit the parquet.

python precompute_oi.py
python precompute_oi.py --date 20260918 --baseline 10
"""
import argparse
import json
from datetime import datetime

import pandas as pd

from oi_fetch import IST, ROOT, build_oi_table, display_frame

OUT_PARQUET = ROOT / "data" / "oi_latest.parquet"
OUT_META = ROOT / "data" / "oi_meta.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYYMMDD; default = latest available")
    ap.add_argument("--baseline", type=int, default=5, help="prior sessions for FUT_OI_RVOL")
    args = ap.parse_args()

    end = (datetime.strptime(args.date, "%Y%m%d").replace(tzinfo=IST)
           if args.date else None)

    df, tdate = build_oi_table(end=end, baseline_sessions=args.baseline)
    df.to_parquet(OUT_PARQUET, index=False)
    OUT_META.write_text(json.dumps({
        "trade_date": f"{tdate:%Y-%m-%d}",
        "generated_at_ist": datetime.now(IST).isoformat(),
        "rows": int(len(df)),
        "baseline_sessions": args.baseline,
    }, indent=2))

    pd.set_option("display.width", 220, "display.float_format", "{:,.2f}".format)
    print(f"Session {tdate:%d-%b-%Y} | {len(df)} symbols -> {OUT_PARQUET.name}")

    print("\nTop 15 long buildups (price up, OI up):")
    lb = df[df["OI_SIGNAL"] == "Long buildup"]
    print(display_frame(lb.sort_values("FUT_CHG_OI_PCT", ascending=False))
          .head(15).to_string(index=False))

    print("\nTop 15 short buildups (price down, OI up):")
    sb = df[df["OI_SIGNAL"] == "Short buildup"]
    print(display_frame(sb.sort_values("FUT_CHG_OI_PCT", ascending=False))
          .head(15).to_string(index=False))

    if "FUT_OI_RVOL" in df.columns and df["FUT_OI_RVOL"].notna().any():
        spike = df[df["FUT_OI_RVOL"].notna()]
        print("\nTop 15 OI spikes vs own baseline:")
        print(display_frame(spike.sort_values("FUT_OI_RVOL", ascending=False))
              .head(15).to_string(index=False))

    print("\nNow commit: git add data/ ; git commit -m \"oi %s\" ; git push"
          % f"{tdate:%d-%b}")


if __name__ == "__main__":
    main()
