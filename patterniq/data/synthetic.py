"""Synthetic OHLC generators: deterministic fixtures for tests and a demo data source."""

from __future__ import annotations

import numpy as np
import pandas as pd

SESSION_OPEN = pd.Timedelta(hours=9, minutes=15)
SESSION_MINUTES = 375  # 09:15 .. 15:29 inclusive, like the NSE cash session

OHLC_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def resample_ohlc(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Aggregate a finer OHLCV frame to 5m / 15m / 1d (bars labelled by their start time)."""
    if timeframe == "1m":
        return df
    rule = {"5m": "5min", "15m": "15min", "1d": "1D"}[timeframe]
    agg = {k: v for k, v in OHLC_AGG.items() if k in df.columns}
    out = df.resample(rule).agg(agg)
    out = out.dropna(subset=["open", "high", "low", "close"])
    out.index.name = df.index.name or "time"
    return out


def path_to_ohlc(
    waypoints: list[tuple[int, float]],
    *,
    seed: int = 0,
    wick: float = 0.25,
    jitter: float = 0.08,
    start: str = "2025-01-06 09:15",
    freq: str = "5min",
) -> pd.DataFrame:
    """Piecewise-linear close path through ``(bar_index, price)`` waypoints, with small noise.

    Used to build textbook-clean formations for tests: ``wick`` and ``jitter`` are in
    price units and should be small relative to the swings.
    """
    rng = np.random.default_rng(seed)
    xs = np.array([w[0] for w in waypoints], float)
    ys = np.array([w[1] for w in waypoints], float)
    n = int(xs[-1]) + 1
    close = np.interp(np.arange(n), xs, ys) + rng.normal(0, jitter, n)
    open_ = np.concatenate([[close[0]], close[:-1]])
    top = np.maximum(open_, close)
    bot = np.minimum(open_, close)
    high = top + np.abs(rng.normal(0, wick, n))
    low = bot - np.abs(rng.normal(0, wick, n))
    idx = pd.date_range(start, periods=n, freq=freq, name="time")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": 1000.0}, index=idx
    )


def candles_to_df(rows: list[tuple[float, float, float, float]], start: str = "2025-01-06 09:15") -> pd.DataFrame:
    """Hand-written (open, high, low, close) rows to a frame with a 5-minute index."""
    idx = pd.date_range(start, periods=len(rows), freq="5min", name="time")
    arr = np.array(rows, float)
    return pd.DataFrame(
        {"open": arr[:, 0], "high": arr[:, 1], "low": arr[:, 2], "close": arr[:, 3], "volume": 1000.0},
        index=idx,
    )


def generate_demo_1m(days: int = 500, seed: int = 7, start: str = "2024-11-01", base: float = 22000.0) -> pd.DataFrame:
    """Nifty-like 1-minute bars: weekdays only, 09:15-15:29, regime-switching volatility,
    a U-shaped intraday volatility profile and overnight gaps.

    Returns are serially independent with no drift, so this is a genuine *null case*: any
    "patterns" in it are chance, and the pattern scorecard should show no real edge. Do not
    read anything into scorecard numbers computed on demo data.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=days)
    t = np.arange(SESSION_MINUTES)
    shape = 1.0 + 1.2 * np.exp(-t / 20.0) + 0.8 * np.exp(-(SESSION_MINUTES - 1 - t) / 25.0)
    shape = shape / np.sqrt(np.mean(shape**2))

    frames = []
    price = base
    z = 0.0
    for d in dates:
        z = 0.9 * z + 0.35 * rng.normal()  # slowly varying volatility regime
        day_sigma = 0.0085 * np.exp(0.4 * z) / np.sqrt(SESSION_MINUTES)
        price *= np.exp(rng.normal(0, 0.003))  # overnight gap
        sig = day_sigma * shape
        rets = sig * rng.normal(size=SESSION_MINUTES)
        close = price * np.exp(np.cumsum(rets))
        open_ = np.concatenate([[price], close[:-1]])
        w = np.abs(rng.normal(0, 0.35, (2, SESSION_MINUTES))) * sig * close
        high = np.maximum(open_, close) + w[0]
        low = np.minimum(open_, close) - w[1]
        vol = rng.integers(2_000, 20_000, SESSION_MINUTES) * (0.5 + shape)
        idx = d + SESSION_OPEN + pd.to_timedelta(t, unit="m")
        frames.append(
            pd.DataFrame(
                {"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx
            )
        )
        price = float(close[-1])
    out = pd.concat(frames)
    out.index.name = "time"
    return out.round(2)
