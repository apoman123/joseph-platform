"""Binance USD-M futures daily trade files for the footprint chart (orderflow_api.py).

fetch_day() downloads one day of aggregated trades from data.binance.vision (public, no account) once, and keeps it
as a parquet file under hub/.cache/binance/. Each trade keeps its aggressor side: +1 buyer crossed the spread,
-1 seller crossed the spread. Needs numpy, pandas and pyarrow (see requirements.txt).
"""
from __future__ import annotations

import io
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

CACHE = Path(__file__).resolve().parent / ".cache" / "binance"
BASE = "https://data.binance.vision/data/futures/um/daily"


def _get(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "tradex/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_day(day: date, kind: str = "aggTrades", symbol: str = "XAUUSDT") -> Path:
    """Download one daily file once and cache it as parquet. Returns the parquet path."""
    if kind != "aggTrades":
        raise ValueError("only aggTrades is supported")
    out = CACHE / kind / f"{symbol}-{kind}-{day.isoformat()}.parquet"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = _get(f"{BASE}/{kind}/{symbol}/{symbol}-{kind}-{day.isoformat()}.zip")
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        df = pd.read_csv(z.open(z.namelist()[0]))
    df = pd.DataFrame({
        "ts": pd.to_datetime(df["transact_time"], unit="ms", utc=True),
        "price": df["price"].astype("float64"),
        "qty": df["quantity"].astype("float64"),
        "side": np.where(df["is_buyer_maker"].astype(str).str.lower() == "true", -1, 1).astype("int8"),
    })
    tmp = out.with_suffix(".part")
    df.to_parquet(tmp, index=False)
    tmp.replace(out)
    return out
