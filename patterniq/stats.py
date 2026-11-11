"""Pattern scorecard: what happened after each pattern, on *your* data.

For every directional, confirmed detection we simulate the most naive trade: enter at the
**open of the bar after confirmation** (you can only act once the confirming bar has closed)
and measure the move over several fixed horizons in the pattern's direction, expressed in
ATR units so 2024's 20k Nifty and today's are comparable.

How to read it
--------------
* ``edge_atr`` is the pattern's mean signed move minus the same-direction average over *all*
  bars. A bullish pattern in a rising market looks good for free; ``edge_atr`` removes that.
* ``edge_se`` is the standard error of the mean. An ``edge_atr`` within about two ``edge_se`` of
  zero is indistinguishable from chance, and with dozens of patterns tested a few rows will
  look significant by luck alone.
* Signals cluster (several patterns fire on the same swing), so rows are not independent.
  Treat small ``n`` and small ``edge_atr`` as noise, and never as a trading recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .models import BEARISH, BULLISH, CONFIRMED, Detection

DEFAULT_HORIZONS = (5, 10, 20)


def forward_moves(df: pd.DataFrame, atr: np.ndarray, horizons) -> dict[int, np.ndarray]:
    """For each bar t: (close[t+1+h-1] - open[t+1]) / atr[t], i.e. enter next open, hold h bars."""
    o = df["open"].to_numpy(float)
    c = df["close"].to_numpy(float)
    n = len(c)
    out = {}
    for h in horizons:
        mv = np.full(n, np.nan)
        # signal on bar t, entry at open[t+1], exit at close[t+h]; needs t+h <= n-1
        t = np.arange(0, max(n - h, 0))
        with np.errstate(invalid="ignore", divide="ignore"):
            mv[t] = (c[t + h] - o[t + 1]) / atr[t]
        out[h] = mv
    return out


@dataclass
class ScoreRow:
    pattern: str
    direction: str
    n: int
    horizon: int
    hit_rate: float
    mean_atr: float
    median_atr: float
    edge_atr: float
    edge_se: float  # standard error of the mean move: |edge| below ~2x this is indistinguishable from noise


def scorecard(
    df: pd.DataFrame,
    detections: list[Detection],
    atr: np.ndarray,
    horizons=DEFAULT_HORIZONS,
    min_n: int = 1,
) -> pd.DataFrame:
    """Aggregate forward moves per (pattern, direction, horizon)."""
    moves = forward_moves(df, atr, horizons)
    finite_atr = np.isfinite(atr) & (atr > 0)
    base = {}
    for h, mv in moves.items():
        ok = np.isfinite(mv) & finite_atr
        base[h] = float(np.mean(mv[ok])) if ok.any() else 0.0

    buckets: dict[tuple[str, str, int], list[float]] = {}
    for d in detections:
        if d.direction not in (BULLISH, BEARISH) or d.status != CONFIRMED:
            continue
        sign = 1.0 if d.direction == BULLISH else -1.0
        for h in horizons:
            v = moves[h][d.confirmed]
            if np.isfinite(v):
                buckets.setdefault((d.pattern, d.direction, h), []).append(sign * v)

    rows: list[ScoreRow] = []
    for (pat, direction, h), vals in buckets.items():
        if len(vals) < min_n:
            continue
        arr = np.array(vals)
        sign = 1.0 if direction == BULLISH else -1.0
        rows.append(
            ScoreRow(
                pattern=pat,
                direction=direction,
                n=len(arr),
                horizon=h,
                hit_rate=float(np.mean(arr > 0)),
                mean_atr=float(np.mean(arr)),
                median_atr=float(np.median(arr)),
                edge_atr=float(np.mean(arr) - sign * base[h]),
                edge_se=float(np.std(arr, ddof=1) / np.sqrt(len(arr))) if len(arr) > 1 else float("nan"),
            )
        )
    cols = [f.name for f in ScoreRow.__dataclass_fields__.values()]
    if not rows:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame([r.__dict__ for r in rows])
    return out.sort_values(["pattern", "direction", "horizon"]).reset_index(drop=True)
