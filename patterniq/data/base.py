"""Data-source protocol and OHLC normalisation shared by every source.

Every source returns a frame indexed by *tz-naive exchange-local time* (``display_tz``)
with float columns open/high/low/close/volume, sorted ascending without duplicates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import numpy as np
import pandas as pd

OHLCV = ["open", "high", "low", "close", "volume"]


@dataclass
class Bounds:
    first: pd.Timestamp
    last: pd.Timestamp
    count: int | None = None


class DataSource(Protocol):
    kind: str

    def timeframes(self) -> list[str]: ...

    def bounds(self, timeframe: str) -> Bounds | None: ...

    def load(
        self,
        timeframe: str,
        *,
        end: datetime | None = None,
        limit: int | None = 500,
        start: datetime | None = None,
    ) -> pd.DataFrame:
        """Bars with ``time <= end`` (default: latest). Without ``start``: the last ``limit`` bars.
        With ``start``: every bar in ``[start, end]`` (capped at ``limit`` when given)."""
        ...


def to_local_naive(values, stored_tz: str, display_tz: str) -> pd.DatetimeIndex:
    """Turn DB / CSV timestamps into tz-naive exchange-local time.

    * tz-aware values are converted to ``display_tz``;
    * naive values are assumed to be in ``stored_tz`` (and converted if that differs);
    * integers are epoch seconds or milliseconds (UTC).
    """
    s = pd.Series(values)
    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
        unit = "ms" if s.abs().max() > 1e11 else "s"
        idx = pd.DatetimeIndex(pd.to_datetime(s, unit=unit, utc=True)).tz_convert(display_tz)
        return idx.tz_localize(None)
    idx = pd.DatetimeIndex(pd.to_datetime(s))
    if idx.tz is not None:
        return idx.tz_convert(display_tz).tz_localize(None)
    if stored_tz != display_tz:
        return idx.tz_localize(stored_tz).tz_convert(display_tz).tz_localize(None)
    return idx


def local_to_stored(ts: datetime, stored_tz: str, display_tz: str, kind: str):
    """Convert a naive display-local datetime to the representation a column of ``kind`` expects."""
    t = pd.Timestamp(ts)
    aware = t.tz_localize(display_tz) if t.tzinfo is None else t.tz_convert(display_tz)
    if kind == "aware":
        return aware.to_pydatetime()
    if kind == "naive":
        return aware.tz_convert(stored_tz).tz_localize(None).to_pydatetime()
    if kind == "date":
        return aware.tz_convert(stored_tz).date()
    if kind == "epoch_s":
        return int(aware.timestamp())
    if kind == "epoch_ms":
        return int(aware.timestamp() * 1000)
    raise ValueError(f"unknown timestamp kind {kind!r}")


def clean_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce to floats, drop broken rows, repair high/low envelopes, sort, de-duplicate."""
    out = df.copy()
    if "volume" not in out.columns:
        out["volume"] = 0.0
    for col in OHLCV:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["open", "high", "low", "close"])
    out = out[(out[["open", "high", "low", "close"]] > 0).all(axis=1)]
    out["volume"] = out["volume"].fillna(0.0)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out["high"] = np.maximum.reduce([out["high"], out["open"], out["close"]])
    out["low"] = np.minimum.reduce([out["low"], out["open"], out["close"]])
    out.index.name = "time"
    return out[OHLCV].astype(float)
