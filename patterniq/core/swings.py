"""ATR-scaled zigzag swing detection that never repaints.

A pivot is only emitted once price has reversed by ``mult * ATR`` away from the extreme,
and each pivot records the bar at which that reversal happened (``confirmed``). Running
the algorithm on ``df[:t]`` therefore yields exactly the pivots of the full run whose
``confirmed < t``: the property the rest of the library relies on.
"""

from __future__ import annotations

import numpy as np

from ..models import Pivot


def zigzag(high: np.ndarray, low: np.ndarray, atr: np.ndarray, mult: float) -> list[Pivot]:
    n = len(high)
    pivots: list[Pivot] = []
    if n < 2:
        return pivots

    direction = 0  # 0 = undecided, 1 = tracking a swing high, -1 = tracking a swing low
    hi_i = lo_i = 0
    ext_i = 0
    ext_p = 0.0

    for i in range(1, n):
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            # Before ATR exists we only keep track of extremes.
            if direction == 0:
                if high[i] > high[hi_i]:
                    hi_i = i
                if low[i] < low[lo_i]:
                    lo_i = i
            continue
        thr = mult * a

        if direction == 0:
            if high[i] > high[hi_i]:
                hi_i = i
            if low[i] < low[lo_i]:
                lo_i = i
            up_move = high[i] - low[lo_i]
            down_move = high[hi_i] - low[i]
            if up_move >= thr and (down_move < thr or lo_i < hi_i or up_move >= down_move):
                pivots.append(Pivot(lo_i, float(low[lo_i]), "L", i))
                # the up-leg's running extreme is the best high since that low
                seg = high[lo_i : i + 1]
                ext_i = lo_i + int(np.argmax(seg))
                ext_p, direction = float(high[ext_i]), 1
            elif down_move >= thr:
                pivots.append(Pivot(hi_i, float(high[hi_i]), "H", i))
                seg = low[hi_i : i + 1]
                ext_i = hi_i + int(np.argmin(seg))
                ext_p, direction = float(low[ext_i]), -1
        elif direction == 1:
            if high[i] > ext_p:
                ext_i, ext_p = i, float(high[i])
            elif ext_p - low[i] >= thr:
                pivots.append(Pivot(ext_i, ext_p, "H", i))
                direction, ext_i, ext_p = -1, i, float(low[i])
        else:
            if low[i] < ext_p:
                ext_i, ext_p = i, float(low[i])
            elif high[i] - ext_p >= thr:
                pivots.append(Pivot(ext_i, ext_p, "L", i))
                direction, ext_i, ext_p = 1, i, float(high[i])
    return pivots


def label_swings(pivots: list[Pivot]) -> list[tuple[Pivot, str]]:
    """Tag each pivot HH / LH (highs) or HL / LL (lows) against the previous same-kind pivot."""
    last: dict[str, Pivot] = {}
    out: list[tuple[Pivot, str]] = []
    for p in pivots:
        prev = last.get(p.kind)
        if prev is None:
            label = ""
        elif p.kind == "H":
            label = "HH" if p.price > prev.price else "LH"
        else:
            label = "HL" if p.price > prev.price else "LL"
        out.append((p, label))
        last[p.kind] = p
    return out
