# nse-delivery-scanner

Ranks NSE cash-market stocks by **delivery volume** from the daily full bhavcopy.

The UDiFF file NSE moved to in July 2024 (`BhavCopy_NSE_CM_*`) has **no delivery
columns**. The only free EOD source with `DELIV_QTY` / `DELIV_PER` is
`sec_bhavdata_full_DDMMYYYY.csv` on nsearchives.

## Files

| File | Role |
|---|---|
| `deliv_fetch.py` | Fetch + parquet cache + ranked table builder |
| `precompute.py`  | Run locally after 7 PM IST, writes `data/delivery_latest.parquet` |
| `app.py`         | Streamlit dashboard, single sortable table |

## Daily flow

```powershell
python precompute.py
& "C:\Program Files\Git\cmd\git.exe" --no-pager add data/delivery_latest.parquet data/delivery_meta.json
& "C:\Program Files\Git\cmd\git.exe" commit -m "delivery snapshot"
& "C:\Program Files\Git\cmd\git.exe" push
```

Local dashboard: `python -m streamlit run app.py`

## Why precompute instead of fetching in the app

Streamlit Community Cloud runs on US datacenter IPs. NSE routinely 403s and
rate-limits those. Fetching on your PC and committing the parquet is the same
pattern already used in `fno-scanner-strategy-update`.

## Columns

- `DELIV_QTY` — shares taken to demat. Ranks largely by float size on its own.
- `DELIV_VAL_CR` — delivery qty x close, in Rs cr. The institutional-footprint read.
- `DELIV_PER` — delivery / traded qty.
- `DELIV_RVOL` — today's delivery vs **median of prior N sessions for that symbol**.
  Cross-sectionally comparable; this is the one that maps to RVOL leadership.

## Gotchas handled

- NSE ships the CSV with a leading space on every column name **and** value.
- `DELIV_QTY` is `"-"` for non-deliverable rows — coerced to NaN, not 0.
- Bare `requests.get` gets 403'd; the session primes cookies off nseindia.com.
- Holidays return a short HTML page with HTTP 200, not a 404 — length-checked.
- BE series is 100% delivery by rule; excluded by default or `DELIV_PER` sorts break.
- `DELIV_RVOL` suppressed when fewer than 3 baseline sessions exist for a symbol.

## Note on interpretation

High delivery on a down day is distribution being absorbed, not accumulation.
The `Direction` filter in the app exists for exactly this — read delivery
spikes together with `CHG_PCT`, never alone.
