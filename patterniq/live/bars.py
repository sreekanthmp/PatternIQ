"""Turning raw 1-minute candles into complete higher-timeframe bars (never partial ones)."""

from __future__ import annotations

import pandas as pd

from .clock import MINUTE, bar_is_closed, bucket_start


def closed_only(df: pd.DataFrame, bar_len: pd.Timedelta, now: pd.Timestamp, delay_s: float = 5.0) -> pd.DataFrame:
    """Drop the bar still in progress (APIs happily return it)."""
    if df.empty:
        return df
    keep = [bar_is_closed(t, bar_len, now, delay_s) for t in df.index]
    return df[keep]


def aggregate(m1: pd.DataFrame, minutes: int, now: pd.Timestamp, delay_s: float = 5.0, grace: pd.Timedelta = pd.Timedelta(minutes=5)) -> pd.DataFrame:
    """Complete ``minutes``-minute bars from closed 1-minute bars, anchored at 09:15.

    A bucket is emitted once its last minute is present, or, if that minute never shows up (data gap),
    once ``grace`` has passed since the bucket ended. Waiting matters because rows are insert-only: a bar
    written one minute early would stay wrong forever.
    """
    cols = ["open", "high", "low", "close", "volume"]
    if m1.empty:
        return pd.DataFrame(columns=cols, index=pd.DatetimeIndex([], name="time"), dtype=float)
    length = pd.Timedelta(minutes=minutes)
    keys = pd.DatetimeIndex([bucket_start(t, minutes) for t in m1.index])
    g = m1.groupby(keys)
    out = pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
        "close": g["close"].last(), "volume": g["volume"].sum(),
    })
    present = set(m1.index)
    keep = []
    for start in out.index:
        end = start + length
        # session buckets never extend past 15:30
        last_minute = min(end, start.normalize() + pd.Timedelta(hours=15, minutes=30)) - MINUTE
        complete = last_minute in present
        keep.append(bar_is_closed(start, length, now, delay_s) and (complete or end + grace <= now))
    out = out[keep]
    out.index.name = "time"
    return out
