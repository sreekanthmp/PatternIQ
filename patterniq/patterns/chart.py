"""Geometric chart patterns built on non-repainting zigzag pivots.

Patterns
--------
* double bottom / double top
* inverse head & shoulders / head & shoulders
* bull flag / bear flag
* ascending / descending / symmetrical triangle, rising / falling wedge,
  rectangle, ascending / descending channel (all two-trendline formations)

Bearish forms are produced by running the bullish detector on a *mirrored* price series
(prices negated, highs and lows swapped) and mapping the result back, so both sides use
exactly the same rules.

A pattern is ``confirmed`` on the first bar that *closes* beyond its trigger line
(neckline / trendline). A setup that is complete but has not triggered yet at the right
edge of the data is reported with ``status="forming"``.

Everything is run at several zigzag scales (multiples of ATR) so both small and large
formations are found, then de-duplicated *causally*: of two overlapping detections the one
confirmed first wins, never one that only appears later.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from ..core.swings import zigzag
from ..models import (
    BEARISH,
    BULLISH,
    CATEGORY_CHART,
    CONFIRMED,
    FORMING,
    NEUTRAL,
    Detection,
    Line,
    Pivot,
)


@dataclass
class ChartParams:
    scales: tuple[float, ...] = (3.0, 6.0)  # zigzag reversal size, in ATR
    breakout_buf_atr: float = 0.1  # close must clear the trigger line by this many ATR
    min_sep: int = 5  # min bars between the two tops / bottoms / shoulders
    double_tol_frac: float = 0.15  # peaks may differ by this fraction of pattern height
    hs_shoulder_tol: float = 0.35  # shoulders may differ by this fraction of head depth
    flag_pole_atr: float = 5.0
    flag_pole_max_bars: int = 40
    flag_max_retrace: float = 0.5
    line_windows: tuple[int, ...] = (5, 6, 7)  # pivots used to fit a two-line formation
    line_min_bars: int = 12
    line_max_bars: int = 500
    line_break_atr: float = 0.5  # trendline breakouts need a more decisive close than a neckline
    flat_frac: float = 0.12
    converge_ratio: float = 0.65
    dedupe_overlap: float = 0.5
    min_quality: float = 0.0


# --------------------------------------------------------------------------------------
# Views and mirroring
# --------------------------------------------------------------------------------------
@dataclass
class View:
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    atr: np.ndarray
    pivots: list[Pivot] = field(default_factory=list)


def mirror_view(v: View) -> View:
    return View(
        open=-v.open,
        high=-v.low,
        low=-v.high,
        close=-v.close,
        atr=v.atr,
        pivots=[
            Pivot(p.idx, -p.price, "L" if p.kind == "H" else "H", p.confirmed) for p in v.pivots
        ],
    )


_MIRROR_NAMES = {
    "double_bottom": "double_top",
    "inverse_head_and_shoulders": "head_and_shoulders",
    "bull_flag": "bear_flag",
}
_PRICE_META = ("target", "stop", "neckline")


def unmirror(d: Detection) -> Detection:
    return replace(
        d,
        pattern=_MIRROR_NAMES[d.pattern],
        direction={BULLISH: BEARISH, BEARISH: BULLISH}.get(d.direction, d.direction),
        price=-d.price,
        points=[(i, -p, lab) for i, p, lab in d.points],
        lines=[Line(l.i1, -l.p1, l.i2, -l.p2, l.role) for l in d.lines],
        meta={k: (-val if k in _PRICE_META and val is not None else val) for k, val in d.meta.items()},
    )


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------
def _first(mask: np.ndarray) -> int | None:
    idx = np.flatnonzero(mask)
    return int(idx[0]) if idx.size else None


def _clip01(x: float) -> float:
    return float(min(1.0, max(0.0, x)))


def _outcome(brk: np.ndarray, inv: np.ndarray, i0: int, anchor: int, max_wait: int, n: int):
    """Resolve a setup scanned over bars ``i0..`` given breakout / invalidation masks.

    Returns (status, bar) with status in {"confirmed", "forming", "dead"}.
    """
    b = _first(brk) if brk.size else None
    v = _first(inv) if inv.size else None
    if b is not None and (v is None or b < v):
        return "confirmed", i0 + b
    if v is not None:
        return "dead", None
    if anchor + max_wait >= n - 1:  # ran out of data before the setup's time budget did
        return FORMING, None
    return "dead", None


def _scan_range(anchor: int, max_wait: int, n: int) -> tuple[int, int]:
    return anchor + 1, min(n - 1, anchor + max_wait)


def _build(
    name: str,
    direction: str,
    status: str,
    start: int,
    end: int,
    trigger: int | None,
    last_pivot_confirmed: int,
    quality: float,
    v: View,
    points,
    lines,
    meta,
) -> Detection:
    n = len(v.close)
    if status == CONFIRMED:
        confirmed = max(trigger, last_pivot_confirmed)
        price = float(v.close[confirmed])
        q = quality
    else:
        confirmed = min(last_pivot_confirmed, n - 1)
        price = float(v.close[n - 1])
        q = quality * 0.8
        direction = NEUTRAL
    return Detection(
        pattern=name,
        category=CATEGORY_CHART,
        direction=direction,
        start=start,
        end=end,
        confirmed=confirmed,
        quality=round(_clip01(q), 3),
        price=price,
        status=status,
        points=points,
        lines=lines,
        meta=meta,
    )


# --------------------------------------------------------------------------------------
# Double bottom (bullish; double top comes from the mirrored view)
# --------------------------------------------------------------------------------------
def double_bottom(v: View, cfg: ChartParams) -> list[Detection]:
    P, n = v.pivots, len(v.close)
    out: list[Detection] = []
    for k in range(3, len(P)):
        prior, a, m, b = P[k - 3], P[k - 2], P[k - 1], P[k]
        if not (a.kind == "L" and m.kind == "H" and b.kind == "L"):
            continue
        at = v.atr[b.idx]
        if not np.isfinite(at):
            continue
        low_ = min(a.price, b.price)
        height = m.price - low_
        tol = max(cfg.double_tol_frac * height, 0.5 * at)
        if abs(a.price - b.price) > tol or b.idx - a.idx < cfg.min_sep:
            continue
        if prior.price < m.price - 0.5 * at:  # needs a decline into the first bottom (within noise)
            continue
        neck = m.price
        width = b.idx - a.idx
        max_wait = max(10, width)
        i0, i1 = _scan_range(b.idx, max_wait, n)
        seg = slice(i0, i1 + 1)
        brk = v.close[seg] > neck + cfg.breakout_buf_atr * v.atr[seg]
        inv = v.close[seg] < low_ - tol
        status, t = _outcome(brk, inv, i0, b.idx, max_wait, n)
        if status == "dead":
            continue
        sym = 1.0 - abs(a.price - b.price) / tol
        body = abs(v.close[t] - v.open[t]) / v.atr[t] if t is not None else 0.0
        q = 0.4 + 0.2 * _clip01(sym) + 0.2 * _clip01(body) + 0.2 * _clip01(height / (4 * at))
        line_end = t if t is not None else n - 1
        out.append(
            _build(
                "double_bottom", BULLISH, status, a.idx, b.idx, t, b.confirmed, q, v,
                [(a.idx, a.price, "1"), (m.idx, m.price, "N"), (b.idx, b.price, "2")],
                [Line(a.idx, neck, line_end, neck, "neckline")],
                {"neckline": neck, "target": neck + height, "stop": low_ - 0.5 * at},
            )
        )
    return out


# --------------------------------------------------------------------------------------
# Inverse head & shoulders (bullish; head & shoulders from the mirrored view)
# --------------------------------------------------------------------------------------
def inverse_head_and_shoulders(v: View, cfg: ChartParams) -> list[Detection]:
    P, n = v.pivots, len(v.close)
    out: list[Detection] = []
    for k in range(5, len(P)):
        pre, ls, n1, hd, n2, rs = P[k - 5 : k + 1]
        if not (
            ls.kind == "L" and n1.kind == "H" and hd.kind == "L" and n2.kind == "H" and rs.kind == "L"
        ):
            continue
        at = v.atr[rs.idx]
        depth = (n1.price + n2.price) / 2 - hd.price
        if not np.isfinite(at) or depth <= 0:
            continue
        if not (hd.price < ls.price and hd.price < rs.price):
            continue
        if min(ls.price, rs.price) - hd.price < max(0.2 * depth, 0.5 * at):
            continue
        if abs(ls.price - rs.price) > cfg.hs_shoulder_tol * depth:
            continue
        d1, d2 = hd.idx - ls.idx, rs.idx - hd.idx
        if d1 < cfg.min_sep or d2 < cfg.min_sep or not (0.4 <= d1 / d2 <= 2.5):
            continue
        if pre.price < n1.price - 0.5 * at:  # needs a decline into the left shoulder (within noise)
            continue
        if abs(n2.price - n1.price) > 0.5 * depth:  # neckline too steep
            continue
        slope = (n2.price - n1.price) / (n2.idx - n1.idx)

        def neck_at(x, n1=n1, slope=slope):
            return n1.price + slope * (x - n1.idx)

        width = rs.idx - ls.idx
        max_wait = max(10, width // 2)
        i0, i1 = _scan_range(rs.idx, max_wait, n)
        xs = np.arange(i0, i1 + 1)
        brk = v.close[xs] > neck_at(xs) + cfg.breakout_buf_atr * v.atr[xs]
        inv = v.close[xs] < rs.price - 0.5 * v.atr[xs]
        status, t = _outcome(brk, inv, i0, rs.idx, max_wait, n)
        if status == "dead":
            continue
        shoulder_sym = 1.0 - abs(ls.price - rs.price) / (cfg.hs_shoulder_tol * depth)
        body = abs(v.close[t] - v.open[t]) / v.atr[t] if t is not None else 0.0
        q = 0.4 + 0.2 * _clip01(shoulder_sym) + 0.2 * _clip01(body) + 0.2 * _clip01(depth / (4 * at))
        line_end = t if t is not None else n - 1
        neck_depth = neck_at(hd.idx) - hd.price
        out.append(
            _build(
                "inverse_head_and_shoulders", BULLISH, status, ls.idx, rs.idx, t, rs.confirmed, q, v,
                [
                    (ls.idx, ls.price, "LS"), (n1.idx, n1.price, "N"), (hd.idx, hd.price, "H"),
                    (n2.idx, n2.price, "N"), (rs.idx, rs.price, "RS"),
                ],
                [Line(n1.idx, n1.price, line_end, neck_at(line_end), "neckline")],
                {
                    "neckline": float(neck_at(line_end)),
                    "target": float(neck_at(line_end) + neck_depth),
                    "stop": float(rs.price - 0.5 * at),
                },
            )
        )
    return out


# --------------------------------------------------------------------------------------
# Bull flag (bullish; bear flag from the mirrored view)
# --------------------------------------------------------------------------------------
def bull_flag(v: View, cfg: ChartParams) -> list[Detection]:
    P, n = v.pivots, len(v.close)
    out: list[Detection] = []
    for j in range(1, len(P)):
        l0, h1 = P[j - 1], P[j]
        if not (l0.kind == "L" and h1.kind == "H"):
            continue
        at = v.atr[h1.idx]
        if not np.isfinite(at):
            continue
        # The pole is the sharp impulse right before the flag top, not necessarily the whole
        # zigzag leg (which can start with a long, slow drift).
        lo_bound = max(l0.idx, h1.idx - cfg.flag_pole_max_bars)
        if h1.idx - lo_bound < 2:
            continue
        ps = lo_bound + int(np.argmin(v.low[lo_bound : h1.idx]))
        ps_price = float(v.low[ps])
        pole = h1.price - ps_price
        pole_bars = h1.idx - ps
        if pole < cfg.flag_pole_atr * at or pole_bars < 2:
            continue
        for m in (3, 5):  # consolidation = L,H,L [,H,L]
            e = j + m
            if e >= len(P):
                break
            cons = P[j + 1 : e + 1]
            if cons[-1].kind != "L":
                continue
            highs = [h1] + [p for p in cons if p.kind == "H"]
            lows = [p for p in cons if p.kind == "L"]
            if max(p.price for p in highs[1:]) > h1.price + 0.5 * at:
                continue
            retrace = h1.price - min(p.price for p in lows)
            if retrace > cfg.flag_max_retrace * pole or min(p.price for p in lows) < ps_price:
                continue
            end_p = cons[-1]
            duration = end_p.idx - h1.idx
            if duration < 4 or duration > 2 * pole_bars + 10:
                continue
            hx = np.array([p.idx - h1.idx for p in highs], float)
            hy = np.array([p.price for p in highs])
            lx = np.array([p.idx - h1.idx for p in lows], float)
            ly = np.array([p.price for p in lows])
            su, iu = np.polyfit(hx, hy, 1)
            sl_, il = np.polyfit(lx, ly, 1)
            if su * duration > 0.5 * at or sl_ * duration > 0.5 * at:  # must drift down/flat
                continue
            h_start = iu - il
            h_end = (su - sl_) * duration + h_start
            if h_start <= 0 or h_end <= 0 or not (0.3 * h_start <= h_end <= 1.3 * h_start):
                continue
            max_wait = max(8, duration)
            i0, i1 = _scan_range(end_p.idx, max_wait, n)
            xs = np.arange(i0, i1 + 1)
            up_line = su * (xs - h1.idx) + iu
            lo_line = sl_ * (xs - h1.idx) + il
            brk = v.close[xs] > up_line + cfg.line_break_atr * v.atr[xs]
            inv = (v.close[xs] < lo_line - 0.5 * v.atr[xs]) | (v.close[xs] < ps_price)
            status, t = _outcome(brk, inv, i0, end_p.idx, max_wait, n)
            if status == "dead":
                continue
            body = abs(v.close[t] - v.open[t]) / v.atr[t] if t is not None else 0.0
            q = (
                0.4
                + 0.2 * _clip01(pole / (8 * at))
                + 0.2 * _clip01(1 - retrace / (cfg.flag_max_retrace * pole))
                + 0.2 * _clip01(body)
            )
            line_end = t if t is not None else n - 1
            up_end = su * (line_end - h1.idx) + iu
            lo_end = sl_ * (line_end - h1.idx) + il
            pts = [(ps, ps_price, "pole")] + [(p.idx, p.price, "") for p in [h1, *cons]]
            out.append(
                _build(
                    "bull_flag", BULLISH, status, ps, end_p.idx, t, end_p.confirmed, q, v, pts,
                    [
                        Line(ps, ps_price, h1.idx, h1.price, "pole"),
                        Line(h1.idx, iu, line_end, up_end, "upper"),
                        Line(h1.idx, il, line_end, lo_end, "lower"),
                    ],
                    {"target": float(up_end + pole), "stop": float(lo_end - 0.5 * at)},
                )
            )
    return out


# --------------------------------------------------------------------------------------
# Two-trendline formations (not mirrored: they are classified by geometry and either
# breakout side is reported)
# --------------------------------------------------------------------------------------
_BIAS = {
    "ascending_triangle": BULLISH,
    "descending_triangle": BEARISH,
    "rising_wedge": BEARISH,
    "falling_wedge": BULLISH,
    "ascending_channel": BULLISH,
    "descending_channel": BEARISH,
}


def _classify(r: float, du: float, dl: float, cfg: ChartParams) -> str | None:
    f = cfg.flat_frac
    u_flat, l_flat = abs(du) <= f, abs(dl) <= f
    u_up, l_up = du > f, dl > f
    u_dn, l_dn = du < -f, dl < -f
    if r <= cfg.converge_ratio:
        if u_flat and l_up:
            return "ascending_triangle"
        if u_dn and l_flat:
            return "descending_triangle"
        if u_dn and l_up:
            return "symmetrical_triangle"
        if u_up and l_up:
            return "rising_wedge"
        if u_dn and l_dn:
            return "falling_wedge"
    elif 0.8 <= r <= 1.25:
        if u_flat and l_flat:
            return "rectangle"
        if u_up and l_up:
            return "ascending_channel"
        if u_dn and l_dn:
            return "descending_channel"
    return None


def two_line_formations(v: View, cfg: ChartParams) -> list[Detection]:
    P, n = v.pivots, len(v.close)
    out: list[Detection] = []
    for w in cfg.line_windows:
        for e in range(w - 1, len(P)):
            Pw = P[e - w + 1 : e + 1]
            highs = [p for p in Pw if p.kind == "H"]
            lows = [p for p in Pw if p.kind == "L"]
            if len(highs) < 2 or len(lows) < 2:
                continue
            start, end = Pw[0].idx, Pw[-1].idx
            width = end - start
            if not (cfg.line_min_bars <= width <= cfg.line_max_bars):
                continue
            ref_atr = float(np.nanmean(v.atr[start : end + 1]))
            if not np.isfinite(ref_atr) or ref_atr <= 0:
                continue
            hx = np.array([p.idx - start for p in highs], float)
            hy = np.array([p.price for p in highs])
            lx = np.array([p.idx - start for p in lows], float)
            ly = np.array([p.price for p in lows])
            su, iu = np.polyfit(hx, hy, 1)
            sl_, il = np.polyfit(lx, ly, 1)
            h0 = iu - il
            h1 = (su - sl_) * width + h0
            if h0 <= 0 or h1 <= 0:
                continue
            hmid = (su - sl_) * width / 2 + h0
            tol = max(0.12 * hmid, 0.6 * ref_atr)
            res = max(
                float(np.max(np.abs(hy - (su * hx + iu)))),
                float(np.max(np.abs(ly - (sl_ * lx + il)))),
            )
            if res > tol:
                continue
            xs_in = np.arange(0, width + 1)
            seg_close = v.close[start : end + 1]
            viol = (seg_close > su * xs_in + iu + 0.5 * ref_atr) | (
                seg_close < sl_ * xs_in + il - 0.5 * ref_atr
            )
            if viol.mean() > 0.05:
                continue
            r = h1 / h0
            du = (su * width) / h0
            dl = (sl_ * width) / h0
            name = _classify(r, du, dl, cfg)
            if name is None:
                continue

            max_wait = max(10, int(0.6 * width))
            i1_cap = n - 1
            if r < 1.0 and su != sl_:
                apex = start + (il - iu) / (su - sl_)
                if apex <= end:
                    continue
                i1_cap = min(i1_cap, int(apex))
            i0 = end + 1
            i1 = min(i1_cap, end + max_wait)
            xs = np.arange(i0, i1 + 1)
            up_line = su * (xs - start) + iu
            lo_line = sl_ * (xs - start) + il
            # A break must clear the line by more than the tolerance used to accept touches,
            # otherwise the *next* touch of a loosely fitted line would register as a breakout.
            buf = np.maximum(cfg.line_break_atr * v.atr[xs], tol)
            bu = _first(v.close[xs] > up_line + buf)
            bd = _first(v.close[xs] < lo_line - buf)

            direction, t = None, None
            if bu is not None and (bd is None or bu <= bd):
                direction, t = BULLISH, i0 + bu
            elif bd is not None:
                direction, t = BEARISH, i0 + bd
            if t is not None:
                status = CONFIRMED
            elif i1 >= n - 1:  # data ended before the setup's time budget / apex did
                status = FORMING
            else:
                continue

            bias = _BIAS.get(name, NEUTRAL)
            with_bias = bias == NEUTRAL or direction == bias
            body = abs(v.close[t] - v.open[t]) / v.atr[t] if t is not None else 0.0
            q = (
                0.3
                + 0.05 * (w - 5)
                + 0.2 * _clip01(1 - res / tol)
                + 0.2 * _clip01(body)
                + 0.15 * (1.0 if with_bias else 0.5)
            )
            line_end = t if t is not None else n - 1
            u_end = su * (line_end - start) + iu
            l_end = sl_ * (line_end - start) + il
            meta = {"bias": bias, "with_bias": bool(with_bias), "pivots": w}
            if t is not None and name in (
                "ascending_triangle", "descending_triangle", "symmetrical_triangle", "rectangle"
            ):
                meta["target"] = float(u_end + h0 if direction == BULLISH else l_end - h0)
            if t is not None:
                meta["stop"] = float(l_end if direction == BULLISH else u_end)
            out.append(
                _build(
                    name, direction or NEUTRAL, status, start, end, t, Pw[-1].confirmed, q, v,
                    [(p.idx, p.price, "") for p in Pw],
                    [
                        Line(start, iu, line_end, u_end, "upper"),
                        Line(start, il, line_end, l_end, "lower"),
                    ],
                    meta,
                )
            )
    return out


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------
def _overlap(a: Detection, b: Detection) -> float:
    a0, a1 = a.span
    b0, b1 = b.span
    inter = max(0, min(a1, b1) - max(a0, b0))
    shorter = min(a1 - a0, b1 - b0)
    return inter / shorter if shorter > 0 else 0.0


def dedupe_causal(dets: list[Detection], overlap: float) -> list[Detection]:
    """Keep the earliest-confirmed of any overlapping group (ties: higher quality).

    Forming setups only survive if no confirmed detection already covers them.
    """
    ordered = sorted(dets, key=lambda d: (d.status == FORMING, d.confirmed, -d.quality, d.pattern))
    kept: list[Detection] = []
    n_confirmed = 0
    for d in ordered:
        if d.status == FORMING:
            clash = any(_overlap(d, k) > overlap for k in kept)
        else:
            # kept confirmed detections are ordered by span end, so walk back until they end
            # before this one starts
            clash = False
            for ki in range(n_confirmed - 1, -1, -1):
                k = kept[ki]
                if k.span[1] < d.span[0]:
                    break
                if _overlap(d, k) > overlap:
                    clash = True
                    break
        if clash:
            continue
        kept.append(d)
        if d.status != FORMING:
            n_confirmed += 1
    kept.sort(key=lambda d: (d.confirmed, d.pattern))
    return kept


def detect_chart_patterns(
    df: pd.DataFrame, atr: np.ndarray, cfg: ChartParams | None = None
) -> list[Detection]:
    cfg = cfg or ChartParams()
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    if len(c) < 30:
        return []
    found: list[Detection] = []
    for scale in cfg.scales:
        v = View(o, h, l, c, atr, zigzag(h, l, atr, scale))
        if len(v.pivots) < 4:
            continue
        mv = mirror_view(v)
        batch: list[Detection] = []
        for fn in (double_bottom, inverse_head_and_shoulders, bull_flag):
            batch += fn(v, cfg)
            batch += [unmirror(d) for d in fn(mv, cfg)]
        batch += two_line_formations(v, cfg)
        for d in batch:
            d.meta["scale"] = scale
        found += batch
    found = [d for d in found if d.quality >= cfg.min_quality]
    return dedupe_causal(found, cfg.dedupe_overlap)
