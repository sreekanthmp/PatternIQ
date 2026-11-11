"""Small, causal indicator helpers (value at bar i only uses bars <= i)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    parts = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    )
    return parts.max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    """Wilder's ATR as a float array (NaN until ``period`` bars are available)."""
    tr = true_range(df)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean().to_numpy(float)


def ema(values: pd.Series, period: int) -> pd.Series:
    return values.ewm(span=period, adjust=False, min_periods=period).mean()
