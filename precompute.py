"""
precompute.py - Run LOCALLY after 7 PM IST, then commit + push.

Why: Streamlit Community Cloud runs on US datacenter IPs and NSE routinely
403s / rate-limits those. Same precompute pattern as fno-scanner-strategy-update:
fetch on your PC, commit the parquet, let the cloud app read the file.

    python precompute.py
    python precompute.py --date 28082026 --baseline 10
"""

import argparse
import json
from datetime import datetime

import pandas as pd

from deliv_fetch import IST, ROOT, build_table, display_frame

OUT_PARQUET = ROOT / "data" / "delivery_latest.parquet"
OUT_META = ROOT / "data" / "delivery_meta.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="DDMMYYYY; default = latest available")
    ap.add_argument("--baseline", type=int, default=5, help="prior sessions for DELIV_RVOL")
    ap.add_argument("--min-turnover", type=float, default=200.0, help="Rs lakhs")
    ap.add_argument("--series", default="EQ", help="comma list, or ALL")
    args = ap.parse_args()

    end = (datetime.strptime(args.date, "%d%m%Y").replace(tzinfo=IST)
           if args.date else None)
    series = () if args.series.upper() == "ALL" else tuple(
        x.strip().upper() for x in args.series.split(","))

    df, tdate = build_table(end=end,
                            baseline_sessions=args.baseline,
                            series=series,
                            min_turnover_lacs=args.min_turnover)

    df.to_parquet(OUT_PARQUET, index=False)
    OUT_META.write_text(json.dumps({
        "trade_date": f"{tdate:%Y-%m-%d}",
        "generated_at_ist": datetime.now(IST).isoformat(),
        "rows": int(len(df)),
        "baseline_sessions": args.baseline,
        "series": list(series) or "ALL",
        "min_turnover_lacs": args.min_turnover,
    }, indent=2))

    pd.set_option("display.width", 220, "display.float_format", "{:,.2f}".format)
    print(f"Session {tdate:%d-%b-%Y} | {len(df)} symbols -> {OUT_PARQUET.name}")
    print("\nTop 15 by delivery value (Rs cr):")
    print(display_frame(df.sort_values("DELIV_VAL_CR", ascending=False))
          .head(15).to_string(index=False))

    if "DELIV_RVOL" in df.columns and df["DELIV_RVOL"].notna().any():
        spike = df[(df["DELIV_VAL_CR"] >= 10) & df["DELIV_RVOL"].notna()]
        print("\nTop 15 delivery spikes vs own baseline:")
        print(display_frame(spike.sort_values("DELIV_RVOL", ascending=False))
              .head(15).to_string(index=False))

    print("\nNow commit:  git add data/ ; git commit -m \"delivery %s\" ; git push"
          % f"{tdate:%d-%b}")


if __name__ == "__main__":
    main()
