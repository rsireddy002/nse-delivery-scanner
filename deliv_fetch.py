"""
deliv_fetch.py - Fetch + cache NSE full bhavcopy (with delivery columns).

The UDiFF file (BhavCopy_NSE_CM_*) has NO delivery data. The only free EOD
source with DELIV_QTY / DELIV_PER is sec_bhavdata_full_DDMMYYYY.csv.

Every fetched day is cached to data/bhav/YYYYMMDD.parquet so repeat runs and
multi-day baselines cost one network call per new session only.
"""

from __future__ import annotations

import io
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

IST = timezone(timedelta(hours=5, minutes=30))
BASE_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d}.csv"

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "data" / "bhav"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/csv,application/csv,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/all-reports",
    "Connection": "keep-alive",
}

NUMERIC_COLS = [
    "PREV_CLOSE", "OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE", "LAST_PRICE",
    "CLOSE_PRICE", "AVG_PRICE", "TTL_TRD_QNTY", "TURNOVER_LACS",
    "NO_OF_TRADES", "DELIV_QTY", "DELIV_PER",
]

_SESSION: requests.Session | None = None


def get_session() -> requests.Session:
    """NSE 403s bare requests. Prime cookies off the main site once, reuse."""
    global _SESSION
    if _SESSION is not None:
        return _SESSION
    s = requests.Session()
    s.headers.update(HEADERS)
    for url in ("https://www.nseindia.com/", "https://www.nseindia.com/all-reports"):
        try:
            s.get(url, timeout=10)
        except requests.RequestException:
            pass
    _SESSION = s
    return s


def _cache_path(d: datetime) -> Path:
    return CACHE_DIR / f"{d:%Y%m%d}.parquet"


def _clean(raw_text: str, d: datetime) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(raw_text))
    # NSE ships this file with a leading space on every column name AND value.
    df.columns = [c.strip() for c in df.columns]
    for c in df.select_dtypes("object").columns:
        df[c] = df[c].astype(str).str.strip()
    for c in NUMERIC_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")  # "-" -> NaN
    df["TRADE_DATE"] = pd.Timestamp(d.date())
    return df


def fetch_day(d: datetime, use_cache: bool = True) -> pd.DataFrame | None:
    """One trading day. Returns None if NSE has not published that date."""
    cp = _cache_path(d)
    if use_cache and cp.exists() and cp.stat().st_size > 1000:
        try:
            return pd.read_parquet(cp)
        except Exception:
            cp.unlink(missing_ok=True)

    if d.weekday() >= 5:
        return None

    url = BASE_URL.format(d=d.strftime("%d%m%Y"))
    try:
        r = get_session().get(url, timeout=25)
    except requests.RequestException:
        return None
    # Holidays return a short HTML error page, not a 404.
    if r.status_code != 200 or len(r.content) < 5000 or "SYMBOL" not in r.text[:400]:
        return None

    df = _clean(r.text, d)
    try:
        df.to_parquet(cp, index=False)
    except Exception:
        pass
    return df


def latest_session(start: datetime | None = None, max_back: int = 10):
    """Walk back to the most recent published bhavcopy. -> (df, date) or (None, None)."""
    d = start or datetime.now(IST)
    for _ in range(max_back):
        df = fetch_day(d)
        if df is not None:
            return df, d
        d -= timedelta(days=1)
    return None, None


def history(end: datetime, sessions: int, max_span: int = 30) -> list[pd.DataFrame]:
    """The `sessions` trading days strictly BEFORE `end`."""
    out, d = [], end - timedelta(days=1)
    while len(out) < sessions and (end - d).days <= max_span:
        df = fetch_day(d)
        if df is not None:
            out.append(df)
        d -= timedelta(days=1)
    return out


def build_table(end: datetime | None = None,
                baseline_sessions: int = 5,
                series: tuple[str, ...] = ("EQ",),
                min_turnover_lacs: float = 200.0) -> tuple[pd.DataFrame, datetime]:
    """Ranked delivery table for the latest (or given) session."""
    today, tdate = latest_session(end)
    if today is None:
        raise RuntimeError("No bhavcopy available. Publishes ~6-8 PM IST.")

    df = today.copy()
    if series:
        # BE series is 100% delivery by rule - excluded or DELIV_PER sorts are junk.
        df = df[df["SERIES"].isin(series)]
    df = df[df["TURNOVER_LACS"] >= min_turnover_lacs]
    df = df.dropna(subset=["DELIV_QTY", "CLOSE_PRICE"])

    df["DELIV_VAL_CR"] = df["DELIV_QTY"] * df["CLOSE_PRICE"] / 1e7
    df["TURNOVER_CR"] = df["TURNOVER_LACS"] / 100.0
    df["CHG_PCT"] = (df["CLOSE_PRICE"] / df["PREV_CLOSE"] - 1) * 100

    if baseline_sessions > 0:
        frames = history(tdate, baseline_sessions)
        if frames:
            hist = pd.concat(frames, ignore_index=True)
            if series:
                hist = hist[hist["SERIES"].isin(series)]
            g = hist.groupby("SYMBOL")["DELIV_QTY"]
            base = g.median().rename("DELIV_BASE").to_frame()
            base["BASE_N"] = g.count()
            df = df.merge(base, on="SYMBOL", how="left")
            # Need a real baseline - 1-2 sessions is noise, not a median.
            ok = df["BASE_N"] >= max(3, baseline_sessions - 2)
            df["DELIV_RVOL"] = (df["DELIV_QTY"] / df["DELIV_BASE"]).where(ok)

    return df.sort_values("DELIV_QTY", ascending=False).reset_index(drop=True), tdate


DISPLAY_COLS = ["SYMBOL", "CLOSE_PRICE", "CHG_PCT", "TTL_TRD_QNTY", "DELIV_QTY",
                "DELIV_PER", "DELIV_VAL_CR", "TURNOVER_CR", "DELIV_RVOL"]


def display_frame(df: pd.DataFrame) -> pd.DataFrame:
    return df[[c for c in DISPLAY_COLS if c in df.columns]]
