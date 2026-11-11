"""Market structure from swing pivots: trend state, break of structure (BOS), change of character (CHoCH).

State after each confirmed pivot comes from the two most recent swing highs and lows:
higher-high + higher-low is an uptrend, lower-high + lower-low a downtrend, anything else a
range. A *cross* of the most recent swing high/low by a closing price is then classified as
continuation (BOS) or potential reversal (CHoCH) depending on that state.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..models import BEARISH, BULLISH, CATEGORY_STRUCTURE, Detection, Line, Pivot

UP, DOWN, RANGE = "uptrend", "downtrend", "range"


def _state(highs: list[Pivot], lows: list[Pivot]) -> str:
    """State from the (up to) two most recent swing highs and swing lows."""
    if len(highs) < 2 or len(lows) < 2:
        return RANGE
    hh = highs[-1].price > highs[-2].price
    hl = lows[-1].price > lows[-2].price
    if hh and hl:
        return UP
    if not hh and not hl:
        return DOWN
    return RANGE


def trend_state(pivots: list[Pivot]) -> str:
    return _state([p for p in pivots if p.kind == "H"][-2:], [p for p in pivots if p.kind == "L"][-2:])


def current_trend(pivots: list[Pivot], close: float | None = None) -> dict:
    highs = [p for p in pivots if p.kind == "H"]
    lows = [p for p in pivots if p.kind == "L"]
    state = trend_state(pivots)
    return {
        "state": state,
        "last_swing_high": highs[-1].price if highs else None,
        "last_swing_low": lows[-1].price if lows else None,
        "invalidation": (lows[-1].price if lows else None) if state == UP else (highs[-1].price if highs else None) if state == DOWN else None,
        "close": close,
    }


def detect_structure_breaks(
    df: pd.DataFrame, atr: np.ndarray, pivots: list[Pivot], min_break_atr: float = 0.1
) -> list[Detection]:
    c = df["close"].to_numpy(float)
    o = df["open"].to_numpy(float)
    n = len(c)
    out: list[Detection] = []
    if not pivots:
        return out

    highs: list[Pivot] = []  # only the two most recent of each kind are ever needed
    lows: list[Pivot] = []
    for k, pv in enumerate(pivots):
        book = highs if pv.kind == "H" else lows
        book.append(pv)
        del book[:-2]
        if not highs or not lows:
            continue
        state = _state(highs, lows)
        last_h, last_l = highs[-1], lows[-1]
        a0 = pv.confirmed + 1
        a1 = (pivots[k + 1].confirmed if k + 1 < len(pivots) else n - 1)
        a1 = min(a1, n - 1)
        if a0 > a1 or a0 < 1:
            continue
        sl = slice(a0, a1 + 1)
        cc, pc, aa = c[sl], c[a0 - 1 : a1], atr[sl]
        with np.errstate(invalid="ignore"):
            up_cross = (cc - last_h.price >= min_break_atr * aa) & (pc <= last_h.price)
            dn_cross = (last_l.price - cc >= min_break_atr * aa) & (pc >= last_l.price)
        ju = int(np.argmax(up_cross)) if up_cross.any() else None
        jd = int(np.argmax(dn_cross)) if dn_cross.any() else None
        if ju is None and jd is None:
            continue
        if jd is None or (ju is not None and ju <= jd):
            j, direction, ref = ju, BULLISH, last_h
        else:
            j, direction, ref = jd, BEARISH, last_l
        i = a0 + j
        if direction == BULLISH:
            name = "choch_bullish" if state == DOWN else "bos_bullish"
        else:
            name = "choch_bearish" if state == UP else "bos_bearish"
        body = abs(c[i] - o[i]) / atr[i]
        push = abs(c[i] - ref.price) / atr[i]
        quality = 0.35 + 0.35 * min(1.0, body) + 0.3 * min(1.0, push)
        out.append(
            Detection(
                pattern=name,
                category=CATEGORY_STRUCTURE,
                direction=direction,
                start=ref.idx,
                end=i,
                confirmed=i,
                quality=round(float(quality), 3),
                price=float(c[i]),
                lines=[Line(ref.idx, ref.price, i, ref.price, "structure")],
                meta={"level": round(ref.price, 2), "state_before": state},
            )
        )
    out.sort(key=lambda d: d.confirmed)
    return out
