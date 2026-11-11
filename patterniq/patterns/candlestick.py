"""Vectorised candlestick pattern detection.

Rules are deliberately explicit and size-aware (relative to ATR) so noise bars on a
1-minute chart are not reported as patterns. Reversal patterns require a prior move in
the opposite direction, measured on the bars *before* the pattern starts.

Every detection is confirmed on the close of its last bar, so nothing looks ahead.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..models import BEARISH, BULLISH, CATEGORY_CANDLE, NEUTRAL, Detection


@dataclass
class CandleParams:
    trend_lookback: int = 6  # bars used to judge the move *into* the pattern
    trend_atr: float = 1.5  # net move (in ATR) needed to call it an up/down trend
    min_range_atr: float = 0.5  # ignore single bars smaller than this many ATR
    long_body_atr: float = 0.8  # "long" body for the first bar of two/three bar patterns


def _shift(a: np.ndarray, k: int) -> np.ndarray:
    out = np.full(a.shape, np.nan)
    if k == 0:
        return a.astype(float)
    out[k:] = a[:-k]
    return out


def _shift_bool(a: np.ndarray, k: int) -> np.ndarray:
    out = np.zeros(a.shape, dtype=bool)
    if k == 0:
        return a.copy()
    out[k:] = a[:-k]
    return out


def detect_candlesticks(
    df: pd.DataFrame, atr: np.ndarray, params: CandleParams | None = None
) -> list[Detection]:
    p = params or CandleParams()
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    a = atr
    n = len(c)
    if n < 5:
        return []

    body = np.abs(c - o)
    rng = h - l
    top = np.maximum(o, c)
    bot = np.minimum(o, c)
    upper = h - top
    lower = bot - l
    bull = c > o
    bear = c < o

    with np.errstate(invalid="ignore"):
        net = c - _shift(c, p.trend_lookback)
        up_at = net >= p.trend_atr * a
        dn_at = net <= -p.trend_atr * a
        up_at = np.where(np.isfinite(a), up_at, False)
        dn_at = np.where(np.isfinite(a), dn_at, False)

    def up(k: int) -> np.ndarray:  # prior up-move before a k-bar pattern
        return _shift_bool(up_at, k)

    def dn(k: int) -> np.ndarray:
        return _shift_bool(dn_at, k)

    with np.errstate(invalid="ignore", divide="ignore"):
        big = rng >= p.min_range_atr * a
        small_body_shape = (body > 0.1 * rng) & (body <= 0.35 * rng)

        # ---- single bar -------------------------------------------------------------
        is_doji = big & (body <= 0.1 * rng)
        dragonfly = is_doji & (upper <= 0.1 * rng) & dn(1)
        gravestone = is_doji & (lower <= 0.1 * rng) & up(1)
        doji = is_doji & ~dragonfly & ~gravestone

        hammer_shape = big & small_body_shape & (lower >= 2 * body) & (upper <= 0.15 * rng)
        inv_shape = big & small_body_shape & (upper >= 2 * body) & (lower <= 0.15 * rng)
        hammer = hammer_shape & dn(1)
        hanging = hammer_shape & up(1)
        inv_hammer = inv_shape & dn(1)
        shooting = inv_shape & up(1)

        maru = (body >= 0.9 * rng) & (rng >= 1.2 * a)
        bull_maru = maru & bull
        bear_maru = maru & bear

        # ---- two bars ---------------------------------------------------------------
        pc, po = _shift(c, 1), _shift(o, 1)
        ph, pl = _shift(h, 1), _shift(l, 1)
        pbody = _shift(body, 1)
        pbull = _shift_bool(bull, 1)
        pbear = _shift_bool(bear, 1)
        pmid = (po + pc) / 2
        long_prev = pbody >= p.long_body_atr * a

        bull_engulf = bull & pbear & (o <= pc) & (c >= po) & (body > pbody) & (body >= 0.5 * a) & dn(2)
        bear_engulf = bear & pbull & (o >= pc) & (c <= po) & (body > pbody) & (body >= 0.5 * a) & up(2)

        piercing = bull & pbear & long_prev & (o <= pc) & (c > pmid) & (c < po) & dn(2)
        dark_cloud = bear & pbull & long_prev & (o >= pc) & (c < pmid) & (c > po) & up(2)

        bull_harami = bull & pbear & long_prev & (o >= pc) & (c <= po) & (body < 0.6 * pbody) & dn(2)
        bear_harami = bear & pbull & long_prev & (o <= pc) & (c >= po) & (body < 0.6 * pbody) & up(2)

        tweezer_bot = bull & pbear & (np.abs(l - pl) <= 0.1 * a) & (body >= 0.3 * a) & dn(2)
        tweezer_top = bear & pbull & (np.abs(h - ph) <= 0.1 * a) & (body >= 0.3 * a) & up(2)

        # ---- three bars -------------------------------------------------------------
        c1, o1, body1 = pc, po, pbody
        c2, o2, body2 = _shift(c, 2), _shift(o, 2), _shift(body, 2)
        mid1 = (o1 + c1) / 2
        mid2 = (o2 + c2) / 2
        bull1, bear1 = pbull, pbear
        bull2, bear2 = _shift_bool(bull, 2), _shift_bool(bear, 2)
        up1, up2 = _shift(upper, 1), _shift(upper, 2)
        lo1, lo2 = _shift(lower, 1), _shift(lower, 2)

        morning = (
            bear2 & (body2 >= p.long_body_atr * a) & (body1 <= 0.3 * body2) & (mid1 < c2)
            & bull & (c > mid2) & dn(3)
        )
        evening = (
            bull2 & (body2 >= p.long_body_atr * a) & (body1 <= 0.3 * body2) & (mid1 > c2)
            & bear & (c < mid2) & up(3)
        )

        soldiers = (
            bull & bull1 & bull2 & (c > c1) & (c1 > c2)
            & (o >= o1) & (o <= c1) & (o1 >= o2) & (o1 <= c2)
            & (body >= 0.5 * a) & (body1 >= 0.5 * a) & (body2 >= 0.5 * a)
            & (upper <= 0.4 * body) & (up1 <= 0.4 * body1) & (up2 <= 0.4 * body2) & ~up(3)
        )
        crows = (
            bear & bear1 & bear2 & (c < c1) & (c1 < c2)
            & (o <= o1) & (o >= c1) & (o1 <= o2) & (o1 >= c2)
            & (body >= 0.5 * a) & (body1 >= 0.5 * a) & (body2 >= 0.5 * a)
            & (lower <= 0.4 * body) & (lo1 <= 0.4 * body1) & (lo2 <= 0.4 * body2) & ~dn(3)
        )

    hh = {k: pd.Series(h).rolling(k).max().to_numpy() for k in (2, 3)}
    ll = {k: pd.Series(l).rolling(k).min().to_numpy() for k in (2, 3)}
    hh[1], ll[1] = h, l

    # (name, direction, bars, mask, context-present array)
    specs = [
        ("doji", NEUTRAL, 1, doji, None),
        ("dragonfly_doji", BULLISH, 1, dragonfly, dn(1)),
        ("gravestone_doji", BEARISH, 1, gravestone, up(1)),
        ("hammer", BULLISH, 1, hammer, dn(1)),
        ("hanging_man", BEARISH, 1, hanging, up(1)),
        ("inverted_hammer", BULLISH, 1, inv_hammer, dn(1)),
        ("shooting_star", BEARISH, 1, shooting, up(1)),
        ("bullish_marubozu", BULLISH, 1, bull_maru, None),
        ("bearish_marubozu", BEARISH, 1, bear_maru, None),
        ("bullish_engulfing", BULLISH, 2, bull_engulf, dn(2)),
        ("bearish_engulfing", BEARISH, 2, bear_engulf, up(2)),
        ("piercing_line", BULLISH, 2, piercing, dn(2)),
        ("dark_cloud_cover", BEARISH, 2, dark_cloud, up(2)),
        ("bullish_harami", BULLISH, 2, bull_harami, dn(2)),
        ("bearish_harami", BEARISH, 2, bear_harami, up(2)),
        ("tweezer_bottom", BULLISH, 2, tweezer_bot, dn(2)),
        ("tweezer_top", BEARISH, 2, tweezer_top, up(2)),
        ("morning_star", BULLISH, 3, morning, dn(3)),
        ("evening_star", BEARISH, 3, evening, up(3)),
        ("three_white_soldiers", BULLISH, 3, soldiers, dn(3)),
        ("three_black_crows", BEARISH, 3, crows, up(3)),
    ]

    usable = np.isfinite(a) & (a > 0)  # degenerate (zero-range / no-ATR) bars never form patterns
    out: list[Detection] = []
    for name, direction, bars, mask, ctx in specs:
        for i in np.flatnonzero(mask & usable):
            i = int(i)
            if i - bars + 1 < 0:
                continue
            size = (hh[bars][i] - ll[bars][i]) / a[i] if np.isfinite(hh[bars][i]) else 0.0
            # how strong was the move *into* the pattern (only for patterns that need one)
            j = i - bars
            strength = 0.0
            if ctx is not None and bool(ctx[i]) and j >= 0 and np.isfinite(net[j]):
                strength = float(np.clip(abs(net[j]) / (3.0 * a[j]), 0.0, 1.0))
            # max 0.70 without a support/resistance level; confluence adds the rest later
            quality = 0.25 + 0.25 * strength + 0.20 * float(np.clip(size / 2.5, 0.0, 1.0))
            out.append(
                Detection(
                    pattern=name,
                    category=CATEGORY_CANDLE,
                    direction=direction,
                    start=i - bars + 1,
                    end=i,
                    confirmed=i,
                    quality=round(quality, 3),
                    price=float(c[i]),
                    meta={"bars": bars},
                )
            )
    out.sort(key=lambda d: (d.confirmed, d.pattern))
    return out
