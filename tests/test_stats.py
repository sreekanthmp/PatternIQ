import numpy as np
import pandas as pd

from patterniq import analyze
from patterniq.models import Detection
from patterniq.stats import forward_moves, scorecard


def tiny_frame():
    idx = pd.date_range("2025-01-06 09:15", periods=8, freq="5min")
    o = np.array([10, 11, 12, 13, 14, 15, 16, 17], float)
    c = o + 0.5
    return pd.DataFrame({"open": o, "high": c + 1, "low": o - 1, "close": c, "volume": 1.0}, index=idx)


def test_forward_moves_enter_next_open_and_exit_on_horizon_close():
    df = tiny_frame()
    mv = forward_moves(df, np.full(8, 2.0), (2,))[2]
    # signal on bar 1 -> enter open[2]=12, exit close[3]=13.5, in ATR units (atr=2)
    assert mv[1] == (13.5 - 12.0) / 2.0
    assert np.isnan(mv[6]) and np.isnan(mv[7])          # not enough bars after the signal


def test_scorecard_signs_by_direction_and_skips_forming_and_neutral():
    df = tiny_frame()
    atr_ = np.full(8, 1.0)
    mk = lambda **kw: Detection(pattern="p", category="candlestick", direction="bullish", start=1, end=1,
                                confirmed=1, quality=0.8, price=11.5, **kw)
    up, down = mk(), Detection(pattern="q", category="candlestick", direction="bearish", start=1, end=1, confirmed=1, quality=0.8, price=11.5)
    skipped = [mk(status="forming"), Detection(pattern="n", category="candlestick", direction="neutral", start=1, end=1, confirmed=1, quality=0.5, price=1)]
    t = scorecard(df, [up, down, *skipped], atr_, (2,))
    assert set(t.pattern) == {"p", "q"}
    p, q = t.set_index("pattern").loc["p"], t.set_index("pattern").loc["q"]
    assert p.mean_atr > 0 > q.mean_atr and p.mean_atr == -q.mean_atr


def test_null_data_shows_no_edge(iid_series):
    """Guards against lookahead creeping in: on i.i.d. returns no pattern may have a real edge."""
    rows = []
    for seed in range(3):
        df = iid_series(40000, seed)
        a = analyze(df)
        rows.append(scorecard(df, a.detections, a.atr, (5, 20), min_n=1).assign(seed=seed))
    t = pd.concat(rows)
    g = t.groupby(["pattern", "direction", "horizon"]).apply(
        lambda x: pd.Series({"n": x.n.sum(), "hit": np.average(x.hit_rate, weights=x.n), "edge": np.average(x.edge_atr, weights=x.n),
                             "se": float(np.sqrt((x.edge_se ** 2 * x.n ** 2).sum()) / x.n.sum())}), include_groups=False)
    g = g[g.n >= 300]
    assert len(g) >= 20
    assert abs(g.hit.mean() - 0.5) < 0.02
    z = (g.edge / g.se).abs()
    assert z.max() < 4.0 and (z > 3).mean() < 0.05
