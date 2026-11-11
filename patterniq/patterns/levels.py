"""Support / resistance levels built from clustered swing pivots.

``LevelTimeline`` recomputes the level set every time a new pivot is confirmed, so the
levels visible at bar *i* only use pivots known at bar *i* (no hindsight). Breakouts and
rejections are then judged against the level set that existed *before* each bar.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..models import BEARISH, BULLISH, CATEGORY_LEVEL, Detection, Level, Line, Pivot


@dataclass
class LevelParams:
    tol_atr: float = 0.5  # pivots within this many ATR of each other share a level
    min_touches: int = 2
    max_pivots: int = 120  # only the most recent swings build levels
    breakout_atr: float = 0.25  # close must clear the level by this much
    rejection_wick_frac: float = 0.35  # wick must be at least this fraction of the bar's range
    cooldown_bars: int = 5
    prior_bars: int = 3  # a breakout needs this many closes on the other side first


def cluster_levels(pivots: list[Pivot], tol: float, min_touches: int) -> list[Level]:
    if not pivots or not np.isfinite(tol) or tol <= 0:
        return []
    ordered = sorted(pivots, key=lambda p: p.price)
    clusters: list[list[Pivot]] = [[ordered[0]]]
    mean = ordered[0].price
    for p in ordered[1:]:
        if p.price - mean <= tol:
            clusters[-1].append(p)
            mean = float(np.mean([q.price for q in clusters[-1]]))
        else:
            clusters.append([p])
            mean = p.price
    levels: list[Level] = []
    newest = max(p.idx for p in pivots)
    oldest = min(p.idx for p in pivots)
    span = max(newest - oldest, 1)
    for cl in clusters:
        if len(cl) < min_touches:
            continue
        last_idx = max(q.idx for q in cl)
        recency = (last_idx - oldest) / span
        strength = min(1.0, 0.2 + 0.2 * min(len(cl) - 2, 2) + 0.2 * recency)
        levels.append(
            Level(
                price=float(np.mean([q.price for q in cl])),
                touches=len(cl),
                first_idx=min(q.idx for q in cl),
                last_idx=last_idx,
                valid_from=max(q.confirmed for q in cl),
                strength=round(strength, 3),
            )
        )
    return levels


class LevelTimeline:
    def __init__(self, pivots: list[Pivot], atr: np.ndarray, params: LevelParams | None = None):
        self.params = params or LevelParams()
        self.pivots = pivots
        self.times: list[int] = []
        self.sets: list[list[Level]] = []
        m = self.params.max_pivots
        for k, p in enumerate(pivots):
            tol = self.params.tol_atr * float(atr[p.confirmed])
            window = pivots[max(0, k - m + 1) : k + 1]
            self.times.append(p.confirmed)
            self.sets.append(cluster_levels(window, tol, self.params.min_touches))

    def at(self, i: int) -> list[Level]:
        """Levels known at the close of bar ``i``."""
        j = bisect_right(self.times, i) - 1
        return self.sets[j] if j >= 0 else []

    def segments(self, n: int):
        """Yield (first_bar, last_bar, levels) where the level set is constant."""
        for k, t in enumerate(self.times):
            start = t + 1
            end = self.times[k + 1] if k + 1 < len(self.times) else n - 1
            if start <= min(end, n - 1):
                yield start, min(end, n - 1), self.sets[k]


def detect_level_events(
    df: pd.DataFrame, atr: np.ndarray, timeline: LevelTimeline
) -> list[Detection]:
    p = timeline.params
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    n = len(c)
    out: list[Detection] = []

    for a0, a1, levels in timeline.segments(n):
        if not levels or a0 < 1:
            continue
        if a0 < p.prior_bars:
            continue
        sl = slice(a0, a1 + 1)
        cc, pc = c[sl], c[a0 - 1 : a1]
        prior = np.stack([c[a0 - k : a1 + 1 - k] for k in range(1, p.prior_bars + 1)])
        pmax, pmin = prior.max(axis=0), prior.min(axis=0)
        oo, hh, ll = o[sl], h[sl], l[sl]
        aa = atr[sl]
        rng = hh - ll
        with np.errstate(invalid="ignore", divide="ignore"):
            upper_frac = (hh - np.maximum(oo, cc)) / rng
            lower_frac = (np.minimum(oo, cc) - ll) / rng
        for lv in levels:
            L = lv.price
            checks = [
                ("resistance_breakout", BULLISH, (cc - L >= p.breakout_atr * aa) & (pc <= L) & (pmax <= L + 0.1 * aa)),
                ("support_breakdown", BEARISH, (L - cc >= p.breakout_atr * aa) & (pc >= L) & (pmin >= L - 0.1 * aa)),
                (
                    "resistance_rejection",
                    BEARISH,
                    (hh >= L - 0.15 * aa) & (cc <= L - 0.25 * aa) & (pc < L) & (cc < oo)
                    & (upper_frac >= p.rejection_wick_frac),
                ),
                (
                    "support_rejection",
                    BULLISH,
                    (ll <= L + 0.15 * aa) & (cc >= L + 0.25 * aa) & (pc > L) & (cc > oo)
                    & (lower_frac >= p.rejection_wick_frac),
                ),
            ]
            for name, direction, mask in checks:
                for j in np.flatnonzero(mask):
                    i = a0 + int(j)
                    wick = upper_frac[j] if name == "resistance_rejection" else lower_frac[j]
                    if name.endswith("breakout") or name.endswith("breakdown"):
                        push = abs(cc[j] - L) / aa[j]
                        feature = min(1.0, push / 1.0)
                    else:
                        feature = float(np.clip(wick, 0.0, 1.0))
                    quality = 0.25 + 0.45 * lv.strength + 0.30 * feature
                    out.append(
                        Detection(
                            pattern=name,
                            category=CATEGORY_LEVEL,
                            direction=direction,
                            start=max(lv.first_idx, 0),
                            end=i,
                            confirmed=i,
                            quality=round(float(quality), 3),
                            price=float(c[i]),
                            lines=[Line(lv.first_idx, L, i, L, "level")],
                            meta={"level": round(L, 2), "touches": lv.touches},
                        )
                    )

    # Overlapping level sets re-fire on the same bar/level; keep the strongest per cooldown.
    out.sort(key=lambda d: (d.pattern, d.confirmed, -d.quality))
    kept: list[Detection] = []
    last: dict[str, Detection] = {}
    for d in out:
        prev = last.get(d.pattern)
        if (
            prev is not None
            and d.confirmed - prev.confirmed <= p.cooldown_bars
            and abs(d.meta["level"] - prev.meta["level"]) <= p.tol_atr * float(atr[d.confirmed])
        ):
            continue
        kept.append(d)
        last[d.pattern] = d
    kept.sort(key=lambda d: (d.confirmed, d.pattern))
    return kept


def reference_levels(df: pd.DataFrame) -> dict:
    """Previous-session high / low / close relative to the last bar (intraday data only)."""
    idx = df.index
    if len(idx) < 2:
        return {}
    days = pd.Series(idx.normalize(), index=idx)
    if days.nunique() < 2:
        return {}
    last_day = days.iloc[-1]
    prev = df[days.to_numpy() < last_day.to_datetime64()]
    if prev.empty:
        return {}
    prev_day = prev.index.normalize()[-1]
    prev = prev[prev.index.normalize() == prev_day]
    return {
        "date": str(prev_day.date()),
        "pdh": float(prev["high"].max()),
        "pdl": float(prev["low"].min()),
        "pdc": float(prev["close"].iloc[-1]),
    }
