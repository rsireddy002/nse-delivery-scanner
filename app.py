"""
app.py - NSE delivery + open-interest scanner.

Tab 1 reads data/delivery_latest.parquet (written by precompute.py).
Tab 2 reads data/oi_latest.parquet (written by precompute_oi.py).
Both fall back to a live fetch when running locally (that call usually
fails on Streamlit Cloud - US datacenter IPs get 403'd by NSE).
"""
import json
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="NSE Delivery + OI Scanner", layout="wide")

ROOT = Path(__file__).resolve().parent

DELIV_PARQUET = ROOT / "data" / "delivery_latest.parquet"
DELIV_META = ROOT / "data" / "delivery_meta.json"

OI_PARQUET = ROOT / "data" / "oi_latest.parquet"
OI_META = ROOT / "data" / "oi_meta.json"


@st.cache_data(ttl=1800)
def load_delivery():
    if DELIV_PARQUET.exists():
        df = pd.read_parquet(DELIV_PARQUET)
        meta = json.loads(DELIV_META.read_text()) if DELIV_META.exists() else {}
        return df, meta, "snapshot"
    from deliv_fetch import build_table  # local-only path
    df, tdate = build_table()
    return df, {"trade_date": f"{tdate:%Y-%m-%d}"}, "live"


@st.cache_data(ttl=1800)
def load_oi():
    if OI_PARQUET.exists():
        df = pd.read_parquet(OI_PARQUET)
        meta = json.loads(OI_META.read_text()) if OI_META.exists() else {}
        return df, meta, "snapshot"
    from oi_fetch import build_oi_table  # local-only path
    df, tdate = build_oi_table()
    return df, {"trade_date": f"{tdate:%Y-%m-%d}"}, "live"


st.title("NSE Delivery + OI Scanner")

tab1, tab2 = st.tabs(["Delivery", "Open Interest"])

# ----------------------------------------------------------------- Delivery
with tab1:
    try:
        df, meta, source = load_delivery()
    except Exception as e:
        st.error(f"No data. Run `python precompute.py` locally and push. ({e})")
        st.stop()

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
    }[c], key="deliv_sort")

    min_val = c2.number_input("Min delivery value (Rs cr)", 0.0, 5000.0, 5.0, 5.0, key="deliv_minval")
    min_pct = c3.slider("Min delivery %", 0, 100, 0, key="deliv_minpct")
    top_n = c4.number_input("Rows", 10, 500, 50, 10, key="deliv_topn")

    view = df.copy()
    view = view[view["DELIV_VAL_CR"] >= min_val]
    view = view[view["DELIV_PER"].fillna(0) >= min_pct]

    direction = st.radio("Direction", ["All", "Up day", "Down day"], horizontal=True, key="deliv_dir")
    if direction == "Up day":
        view = view[view["CHG_PCT"] > 0]
    elif direction == "Down day":
        view = view[view["CHG_PCT"] < 0]

    if sort_by == "DELIV_RVOL" and "DELIV_RVOL" in view.columns:
        view = view[view["DELIV_RVOL"].notna()]
    if sort_by not in view.columns:
        st.warning(f"{sort_by} not in this snapshot - rebuild with a baseline.")
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
                        f"delivery_{meta.get('trade_date','')}.csv", "text/csv",
                        key="deliv_dl")

    st.caption(
        "Delivery RVOL = today's delivery qty / median of prior sessions. Absolute "
        "delivery qty mostly ranks by float size; delivery value and RVOL carry the "
        "signal. BE series excluded (100% delivery by rule)."
    )

# ---------------------------------------------------------------- Open Interest
with tab2:
    try:
        odf, ometa, osource = load_oi()
    except Exception as e:
        st.error(f"No data. Run `python precompute_oi.py` locally and push. ({e})")
        st.stop()

    st.caption(
        f"Session {ometa.get('trade_date', '?')} · {len(odf)} symbols · "
        f"source: {osource} · baseline {ometa.get('baseline_sessions', '?')} sessions"
    )

    oc1, oc2, oc3 = st.columns(3)
    osort_by = oc1.selectbox("Rank by", [
        "FUT_CHG_OI_PCT", "FUT_OI_RVOL", "FUT_CHG_OI", "FUT_OI", "PCR",
    ], format_func=lambda c: {
        "FUT_CHG_OI_PCT": "Futures OI change %",
        "FUT_OI_RVOL": "Futures OI RVOL",
        "FUT_CHG_OI": "Futures OI change (qty)",
        "FUT_OI": "Futures OI (qty)",
        "PCR": "Put-call ratio",
    }[c], key="oi_sort")

    signal = oc2.multiselect(
        "Signal",
        ["Long buildup", "Short buildup", "Short covering", "Long unwinding"],
        default=["Long buildup", "Short buildup", "Short covering", "Long unwinding"],
        key="oi_signal",
    )
    otop_n = oc3.number_input("Rows", 10, 500, 50, 10, key="oi_topn")

    oview = odf.copy()
    if "OI_SIGNAL" in oview.columns and signal:
        oview = oview[oview["OI_SIGNAL"].isin(signal)]

    if osort_by == "FUT_OI_RVOL" and "FUT_OI_RVOL" in oview.columns:
        oview = oview[oview["FUT_OI_RVOL"].notna()]
    if osort_by not in oview.columns:
        st.warning(f"{osort_by} not in this snapshot - rebuild with a baseline.")
        osort_by = "FUT_CHG_OI_PCT"

    oview = oview.reindex(oview[osort_by].abs().sort_values(ascending=False).index).head(int(otop_n))

    ocols = [c for c in ["SYMBOL", "FUT_CLOSE", "FUT_CHG_PCT", "FUT_OI", "FUT_CHG_OI",
                          "FUT_CHG_OI_PCT", "FUT_OI_RVOL", "OI_SIGNAL",
                          "CE_OI", "CE_CHG_OI", "PE_OI", "PE_CHG_OI", "PCR"]
             if c in oview.columns]

    st.dataframe(
        oview[ocols],
        use_container_width=True,
        hide_index=True,
        column_config={
            "SYMBOL": st.column_config.TextColumn("Symbol", width="small"),
            "FUT_CLOSE": st.column_config.NumberColumn("Fut close", format="%.2f"),
            "FUT_CHG_PCT": st.column_config.NumberColumn("Price chg %", format="%.2f"),
            "FUT_OI": st.column_config.NumberColumn("Fut OI", format="%d"),
            "FUT_CHG_OI": st.column_config.NumberColumn("Fut OI chg", format="%d"),
            "FUT_CHG_OI_PCT": st.column_config.NumberColumn("Fut OI chg %", format="%.2f"),
            "FUT_OI_RVOL": st.column_config.NumberColumn("Fut OI RVOL", format="%.2f"),
            "OI_SIGNAL": st.column_config.TextColumn("Signal"),
            "CE_OI": st.column_config.NumberColumn("Call OI", format="%d"),
            "CE_CHG_OI": st.column_config.NumberColumn("Call OI chg", format="%d"),
            "PE_OI": st.column_config.NumberColumn("Put OI", format="%d"),
            "PE_CHG_OI": st.column_config.NumberColumn("Put OI chg", format="%d"),
            "PCR": st.column_config.NumberColumn("PCR", format="%.2f"),
        },
    )

    st.download_button("Download CSV", oview[ocols].to_csv(index=False),
                        f"oi_{ometa.get('trade_date','')}.csv", "text/csv",
                        key="oi_dl")

    st.caption(
        "Signal = price direction x futures OI direction: Long buildup (price up, "
        "OI up), Short covering (price up, OI down), Short buildup (price down, OI "
        "up), Long unwinding (price down, OI down). Fut OI RVOL = today's futures OI "
        "/ median of prior sessions for that symbol's near-month contract. PCR = put "
        "OI / call OI at the nearest expiry, for read direction only - it doesn't "
        "carry a trade signal on its own."
    )
