"""NSE cash-session clock in Asia/Kolkata. All timestamps here are tz-naive IST wall-clock, like the rest of PatternIQ."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

IST = ZoneInfo("Asia/Kolkata")
OPEN = pd.Timedelta(hours=9, minutes=15)
CLOSE = pd.Timedelta(hours=15, minutes=30)
MINUTE = pd.Timedelta(minutes=1)


def ist_now() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(IST).replace(tzinfo=None))


def is_weekday(ts: pd.Timestamp) -> bool:
    return ts.weekday() < 5


def session_open(day: pd.Timestamp) -> pd.Timestamp:
    return day.normalize() + OPEN


def session_close(day: pd.Timestamp) -> pd.Timestamp:
    return day.normalize() + CLOSE


def market_open(now: pd.Timestamp) -> bool:
    return is_weekday(now) and session_open(now) <= now < session_close(now)


def next_open(now: pd.Timestamp) -> pd.Timestamp:
    day = now.normalize()
    if not (is_weekday(now) and now < session_open(now)):
        day += pd.Timedelta(days=1)
    while not is_weekday(day):
        day += pd.Timedelta(days=1)
    return session_open(day)


def bar_is_closed(bar_start: pd.Timestamp, bar_len: pd.Timedelta, now: pd.Timestamp, delay_s: float = 3.0) -> bool:
    """A bar is complete once its end plus a small settling delay has passed."""
    return bar_start + bar_len + pd.Timedelta(seconds=delay_s) <= now


def bucket_start(ts: pd.Timestamp, minutes: int) -> pd.Timestamp:
    """Start of the ``minutes``-long session bucket containing ``ts`` (buckets are anchored at 09:15)."""
    anchor = session_open(ts)
    n = int((ts - anchor) // pd.Timedelta(minutes=minutes))
    return anchor + n * pd.Timedelta(minutes=minutes)
