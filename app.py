"""
app.py - NSE delivery scanner. One clean table.

Reads data/delivery_latest.parquet written by precompute.py. Falls back to a
live fetch when running locally (that call usually fails on Streamlit Cloud).
"""

import json
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="NSE Delivery Scanner", layout="wide")

ROOT = Path(__file__).resolve().parent
PARQUET = ROOT / "data" / "delivery_latest.parquet"
META = ROOT / "data" / "delivery_meta.json"


@st.cache_data(ttl=1800)
def load():
    if PARQUET.exists():
        df = pd.read_parquet(PARQUET)
        meta = json.loads(META.read_text()) if META.exists() else {}
        return df, meta, "snapshot"
    from deliv_fetch import build_table  # local-only path
    df, tdate = build_table()
    return df, {"trade_date": f"{tdate:%Y-%m-%d}"}, "live"


try:
    df, meta, source = load()
except Exception as e:
    st.error(f"No data. Run `python precompute.py` locally and push. ({e})")
    st.stop()

st.title("NSE Delivery Scanner")
st.caption(
    f"Session {meta.get('trade_date', '?')} · {len(df)} symbols · "
    f"{meta.get('series', 'EQ')} series · source: {source} · "
    f"baseline {meta.get('baseline_sessions', '?')} sessions"
)

c1, c2, c3, c4 = st.columns(4)
sort_by = c1.selectbox("Rank by", [
    "DELIV_VAL_CR", "DELIV_QTY", "DELIV_RVOL", "DELIV_PER", "TURNOVER_CR", "CHG_PCT",
], format_func=lambda c: {
    "DELIV_VAL_CR": "Delivery value (Rs cr)",
    "DELIV_QTY": "Delivery quantity",
    "DELIV_RVOL": "Delivery RVOL",
    "DELIV_PER": "Delivery %",
    "TURNOVER_CR": "Turnover (Rs cr)",
    "CHG_PCT": "Day change %",
}[c])
min_val = c2.number_input("Min delivery value (Rs cr)", 0.0, 5000.0, 5.0, 5.0)
min_pct = c3.slider("Min delivery %", 0, 100, 0)
top_n = c4.number_input("Rows", 10, 500, 50, 10)

view = df.copy()
view = view[view["DELIV_VAL_CR"] >= min_val]
view = view[view["DELIV_PER"].fillna(0) >= min_pct]

direction = st.radio("Direction", ["All", "Up day", "Down day"], horizontal=True)
if direction == "Up day":
    view = view[view["CHG_PCT"] > 0]
elif direction == "Down day":
    view = view[view["CHG_PCT"] < 0]

if sort_by == "DELIV_RVOL" and "DELIV_RVOL" in view.columns:
    view = view[view["DELIV_RVOL"].notna()]

if sort_by not in view.columns:
    st.warning(f"{sort_by} not in this snapshot — rebuild with a baseline.")
    sort_by = "DELIV_VAL_CR"

view = view.sort_values(sort_by, ascending=False).head(int(top_n))

cols = [c for c in ["SYMBOL", "CLOSE_PRICE", "CHG_PCT", "TTL_TRD_QNTY", "DELIV_QTY",
                    "DELIV_PER", "DELIV_VAL_CR", "TURNOVER_CR", "DELIV_RVOL"]
        if c in view.columns]

st.dataframe(
    view[cols],
    use_container_width=True,
    hide_index=True,
    column_config={
        "SYMBOL": st.column_config.TextColumn("Symbol", width="small"),
        "CLOSE_PRICE": st.column_config.NumberColumn("Close", format="%.2f"),
        "CHG_PCT": st.column_config.NumberColumn("Chg %", format="%.2f"),
        "TTL_TRD_QNTY": st.column_config.NumberColumn("Volume", format="%d"),
        "DELIV_QTY": st.column_config.NumberColumn("Deliv qty", format="%d"),
        "DELIV_PER": st.column_config.NumberColumn("Deliv %", format="%.1f"),
        "DELIV_VAL_CR": st.column_config.NumberColumn("Deliv Rs cr", format="%.1f"),
        "TURNOVER_CR": st.column_config.NumberColumn("Turnover cr", format="%.1f"),
        "DELIV_RVOL": st.column_config.NumberColumn("Deliv RVOL", format="%.2f"),
    },
)

st.download_button("Download CSV", view[cols].to_csv(index=False),
                   f"delivery_{meta.get('trade_date','')}.csv", "text/csv")

st.caption(
    "Delivery RVOL = today's delivery qty / median of prior sessions. Absolute "
    "delivery qty mostly ranks by float size; delivery value and RVOL carry the "
    "signal. BE series excluded (100% delivery by rule)."
)
