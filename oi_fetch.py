"""
oi_fetch.py - Fetch + cache NSE F&O bhavcopy (UDiFF format) and aggregate
open interest / change-in-OI to symbol level.

Unlike delivery %, OI and change-in-OI are native columns in the F&O bhavcopy
(OpnIntrst, ChngInOpnIntrst) - no manual day-over-day diffing needed. The file
is a zip (unlike sec_bhavdata_full, which is plain CSV).

Every fetched day is cached to data/fo_bhav/YYYYMMDD.parquet so repeat runs
and multi-day baselines cost one network call per new session only.

NOTE: NSE has changed this schema before (UDiFF migration, July 2024). If
column names below don't match on first live run, open one cached/raw pull
and adjust NUMERIC_COLS / column names accordingly.
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# Reuse the cookie-primed session + IST tz from the delivery scanner so NSE
# doesn't 403 us twice over, and so both scripts hit the same session cache.
from deliv_fetch import IST, get_session

BASE_URL = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d}_F_0000.csv.zip"

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "data" / "fo_bhav"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

FUT_TYPES = {"STF", "IDF"}   # stock futures, index futures
OPT_TYPES = {"STO", "IDO"}   # stock options, index options

NUMERIC_COLS = [
    "StrkPric", "OpnPric", "HghPric", "LwPric", "ClsPric", "LastPric",
    "PrvsClsgPric", "UndrlygPric", "SttlmPric", "OpnIntrst",
    "ChngInOpnIntrst", "TtlTradgVol", "TtlTrfVal", "TtlNbOfTxsExctd",
    "NewBrdLotQty",
]


def _cache_path(d: datetime) -> Path:
    return CACHE_DIR / f"{d:%Y%m%d}.parquet"


def _clean(raw_text: str, d: datetime) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(raw_text))
    df.columns = [c.strip() for c in df.columns]
    for c in df.select_dtypes("object").columns:
        df[c] = df[c].astype(str).str.strip()
    for c in NUMERIC_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ("XpryDt", "TradDt"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce", dayfirst=True)
    df["TRADE_DATE"] = pd.Timestamp(d.date())
    return df


def fetch_day(d: datetime, use_cache: bool = True) -> pd.DataFrame | None:
    """One trading day of raw F&O bhavcopy. None if NSE hasn't published it."""
    cp = _cache_path(d)
    if use_cache and cp.exists() and cp.stat().st_size > 1000:
        try:
            return pd.read_parquet(cp)
        except Exception:
            cp.unlink(missing_ok=True)

    if d.weekday() >= 5:
        return None

    url = BASE_URL.format(d=d.strftime("%Y%m%d"))
    try:
        r = get_session().get(url, timeout=25)
    except Exception:
        return None

    # Holidays / not-yet-published usually 403 or return a tiny error body.
    if r.status_code != 200 or len(r.content) < 5000:
        return None

    try:
        z = zipfile.ZipFile(io.BytesIO(r.content))
        raw_text = z.read(z.namelist()[0]).decode("utf-8")
    except (zipfile.BadZipFile, IndexError):
        return None

    if "TckrSymb" not in raw_text[:400] and "OpnIntrst" not in raw_text[:400]:
        return None

    df = _clean(raw_text, d)
    try:
        df.to_parquet(cp, index=False)
    except Exception:
        pass
    return df


def latest_session(start: datetime | None = None, max_back: int = 10):
    d = start or datetime.now(IST)
    for _ in range(max_back):
        df = fetch_day(d)
        if df is not None:
            return df, d
        d -= timedelta(days=1)
    return None, None


def history(end: datetime, sessions: int, max_span: int = 30) -> list[pd.DataFrame]:
    out, d = [], end - timedelta(days=1)
    while len(out) < sessions and (end - d).days <= max_span:
        df = fetch_day(d)
        if df is not None:
            out.append(df)
        d -= timedelta(days=1)
    return out


def _near_month(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only the nearest expiry per symbol (current-month future /
    current-week-or-month option), so we're not summing OI across expiries."""
    near = df.groupby("TckrSymb")["XpryDt"].transform("min")
    return df[df["XpryDt"] == near]


def _classify(price_up: pd.Series, oi_up: pd.Series) -> pd.Series:
    return pd.Series(
        [
            "Long buildup" if p and o else
            "Short covering" if p and not o else
            "Short buildup" if not p and o else
            "Long unwinding"
            for p, o in zip(price_up, oi_up)
        ],
        index=price_up.index,
    )


def build_oi_table(end: datetime | None = None,
                    baseline_sessions: int = 5) -> tuple[pd.DataFrame, datetime]:
    """Symbol-level futures + options OI-change table for the latest session."""
    today, tdate = latest_session(end)
    if today is None:
        raise RuntimeError("No F&O bhavcopy available. Publishes ~6-8 PM IST.")

    fut = _near_month(today[today["FinInstrmTp"].isin(FUT_TYPES)].copy())
    fut = fut.dropna(subset=["OpnIntrst", "ClsPric", "PrvsClsgPric"])
    fut["FUT_CHG_PCT"] = (fut["ClsPric"] / fut["PrvsClsgPric"] - 1) * 100
    fut_tbl = fut.rename(columns={
        "TckrSymb": "SYMBOL", "ClsPric": "FUT_CLOSE",
        "OpnIntrst": "FUT_OI", "ChngInOpnIntrst": "FUT_CHG_OI",
    })[["SYMBOL", "FUT_CLOSE", "FUT_CHG_PCT", "FUT_OI", "FUT_CHG_OI"]]
    fut_tbl["FUT_CHG_OI_PCT"] = (
        fut_tbl["FUT_CHG_OI"] / (fut_tbl["FUT_OI"] - fut_tbl["FUT_CHG_OI"]).replace(0, pd.NA) * 100
    )
    fut_tbl["OI_SIGNAL"] = _classify(fut_tbl["FUT_CHG_PCT"] > 0, fut_tbl["FUT_CHG_OI"] > 0)

    opt = _near_month(today[today["FinInstrmTp"].isin(OPT_TYPES)].copy())
    opt = opt.dropna(subset=["OpnIntrst"])
    opt_g = (
        opt.groupby(["TckrSymb", "OptnTp"])[["OpnIntrst", "ChngInOpnIntrst"]]
        .sum().unstack("OptnTp")
    )
    opt_g.columns = [f"{typ}_OI" if metric == "OpnIntrst" else f"{typ}_CHG_OI"
                     for metric, typ in opt_g.columns]
    opt_g = opt_g.rename_axis("SYMBOL").reset_index()
    if "PE_OI" in opt_g.columns and "CE_OI" in opt_g.columns:
        opt_g["PCR"] = opt_g["PE_OI"] / opt_g["CE_OI"].replace(0, pd.NA)

    df = fut_tbl.merge(opt_g, on="SYMBOL", how="outer")

    if baseline_sessions > 0:
        frames = history(tdate, baseline_sessions)
        if frames:
            hist = pd.concat(frames, ignore_index=True)
            hist_fut = _near_month(hist[hist["FinInstrmTp"].isin(FUT_TYPES)])
            g = hist_fut.groupby("TckrSymb")["OpnIntrst"]
            base = g.median().rename("FUT_OI_BASE").to_frame()
            base["BASE_N"] = g.count()
            base = base.rename_axis("SYMBOL").reset_index()
            df = df.merge(base, on="SYMBOL", how="left")
            ok = df["BASE_N"] >= max(3, baseline_sessions - 2)
            df["FUT_OI_RVOL"] = (df["FUT_OI"] / df["FUT_OI_BASE"]).where(ok)

    return df.sort_values("FUT_CHG_OI_PCT", ascending=False).reset_index(drop=True), tdate


DISPLAY_COLS = ["SYMBOL", "FUT_CLOSE", "FUT_CHG_PCT", "FUT_OI", "FUT_CHG_OI",
                "FUT_CHG_OI_PCT", "FUT_OI_RVOL", "OI_SIGNAL",
                "CE_OI", "CE_CHG_OI", "PE_OI", "PE_CHG_OI", "PCR"]


def display_frame(df: pd.DataFrame) -> pd.DataFrame:
    return df[[c for c in DISPLAY_COLS if c in df.columns]]
