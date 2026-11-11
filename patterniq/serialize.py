"""Turn analysis results into JSON-friendly structures for the API and CLI.

Chart times are epoch seconds of the *exchange-local wall clock read as if it were UTC*.
That is the trick TradingView's lightweight-charts needs to show 09:15 as 09:15 in IST
without a timezone plugin: the frontend formats these values with UTC.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .analysis import Analysis
from .models import Detection, Level, Pivot
from .patterns.catalog import CATALOG


def epoch_seconds(index: pd.DatetimeIndex) -> np.ndarray:
    return ((index - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)).to_numpy(dtype="int64")


def _clean(x: Any) -> Any:
    if isinstance(x, (np.floating, float)):
        f = float(x)
        return None if math.isnan(f) or math.isinf(f) else round(f, 4)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    return x


def _line_value(i1: int, p1: float, i2: int, p2: float, i: int) -> float:
    return p1 if i2 == i1 else p1 + (p2 - p1) * (i - i1) / (i2 - i1)


def detection_to_dict(d: Detection, times: np.ndarray, first_visible: int = 0) -> dict:
    info = CATALOG.get(d.pattern)
    n = len(times)

    def t(i: int) -> int:
        return int(times[min(max(i, 0), n - 1)])

    lines = []
    for ln in d.lines:
        i1, p1, i2, p2 = ln.i1, ln.p1, min(ln.i2, n - 1), ln.p2
        if i2 < first_visible:
            continue
        if i1 < first_visible:  # clip to the visible window so the chart's time axis is not stretched
            p1 = _line_value(ln.i1, ln.p1, ln.i2, ln.p2, first_visible)
            i1 = first_visible
        lines.append({"t1": t(i1), "p1": p1, "t2": t(i2), "p2": p2, "role": ln.role})

    return _clean(
        {
            "id": f"{d.pattern}:{d.start}:{d.confirmed}",
            "pattern": d.pattern,
            "label": info.label if info else d.pattern,
            "category": d.category,
            "direction": d.direction,
            "status": d.status,
            "quality": d.quality,
            "start_time": t(max(d.start, first_visible)),
            "end_time": t(d.end),
            "time": t(d.confirmed),
            "price": d.price,
            "points": [
                {"time": t(i), "price": p, "label": lab} for i, p, lab in d.points if i >= first_visible
            ],
            "lines": lines,
            "meta": d.meta,
        }
    )


def level_to_dict(lv: Level, price: float, times: np.ndarray, first_visible: int) -> dict:
    n = len(times)
    return _clean(
        {
            "price": lv.price,
            "touches": lv.touches,
            "strength": lv.strength,
            "kind": lv.kind_vs(price),
            "from_time": int(times[min(max(lv.first_idx, first_visible), n - 1)]),
        }
    )


def pick_levels(levels: list[Level], price: float, atr: float, limit: int = 10, reach_atr: float = 40.0) -> list[Level]:
    """Strongest levels that are not absurdly far from the current price."""
    near = [lv for lv in levels if abs(lv.price - price) <= reach_atr * atr] if atr and atr > 0 else list(levels)
    near.sort(key=lambda lv: (-lv.strength, abs(lv.price - price)))
    return sorted(near[:limit], key=lambda lv: lv.price)


def swing_to_dict(p: Pivot, label: str, times: np.ndarray) -> dict:
    return {"time": int(times[p.idx]), "price": p.price, "kind": p.kind, "label": label}


def analysis_payload(
    df: pd.DataFrame, a: Analysis, first_visible: int, *, min_quality: float = 0.0, include_forming: bool = True
) -> dict:
    """Everything the chart needs for ``df.iloc[first_visible:]`` (earlier bars are warm-up only)."""
    times = epoch_seconds(df.index)
    view = df.iloc[first_visible:]
    last_close = float(df["close"].iloc[-1])
    last_atr = float(a.atr[-1]) if len(a.atr) else float("nan")

    dets = [
        d
        for d in a.detections
        if d.confirmed >= first_visible
        and d.quality >= min_quality
        and (include_forming or d.status == "confirmed")
    ]
    candles = [
        {"time": int(t), "open": o, "high": h, "low": l, "close": c, "volume": v}
        for t, o, h, l, c, v in zip(
            times[first_visible:], view["open"], view["high"], view["low"], view["close"], view["volume"]
        )
    ]
    return {
        "candles": _clean(candles),
        "detections": [detection_to_dict(d, times, first_visible) for d in dets],
        "levels": [
            level_to_dict(lv, last_close, times, first_visible)
            for lv in pick_levels(a.levels, last_close, last_atr)
        ],
        "swings": [
            swing_to_dict(p, lab, times) for p, lab in a.swing_labels if p.idx >= first_visible and lab
        ],
        "trend": _clean(a.trend),
        "reference": _clean(a.reference),
        "atr": _clean(last_atr),
    }
