import numpy as np

from patterniq import analyze
from patterniq.core.indicators import atr
from patterniq.core.swings import zigzag
from patterniq.data.synthetic import path_to_ohlc
from patterniq.patterns.levels import LevelTimeline, cluster_levels
from patterniq.patterns.structure import current_trend, trend_state


def test_cluster_levels_merges_nearby_pivots_and_requires_touches():
    from patterniq.models import Pivot

    piv = [Pivot(10, 100.0, "H", 15), Pivot(40, 100.4, "H", 45), Pivot(70, 99.8, "H", 75), Pivot(90, 120.0, "H", 95)]
    levels = cluster_levels(piv, tol=0.6, min_touches=2)
    assert len(levels) == 1
    assert levels[0].touches == 3 and abs(levels[0].price - 100.07) < 0.1
    assert levels[0].valid_from == 75  # only usable once the 3rd touch was known


def test_level_timeline_only_knows_touches_that_already_happened():
    df = path_to_ohlc([(0, 100), (30, 100), (50, 110), (70, 100), (90, 110), (110, 100), (130, 110), (150, 100)], seed=1)
    a = atr(df)
    piv = zigzag(df.high.to_numpy(), df.low.to_numpy(), a, 3.0)
    tl = LevelTimeline(piv, a)
    for t in range(0, len(df), 7):                       # no level is ever visible before its last touch is confirmed
        assert all(lv.valid_from <= t for lv in tl.at(t))
    third = [p for p in piv if p.kind == "H"][-1]
    near = lambda levels: [lv for lv in levels if abs(lv.price - 110) < 1]
    assert all(lv.last_idx < third.idx for lv in near(tl.at(third.confirmed - 1)))
    assert any(lv.last_idx == third.idx for lv in near(tl.at(third.confirmed)))


def test_resistance_breakout_after_two_tests():
    wp = [(0, 100), (30, 100), (50, 110), (70, 100), (90, 110), (110, 101), (130, 118), (140, 121)]
    df = path_to_ohlc(wp, seed=1)
    dets = [d for d in analyze(df, categories=["level"]).detections if d.pattern == "resistance_breakout"]
    assert dets, "expected a breakout of the 110 resistance"
    d = dets[0]
    assert 108 < d.meta["level"] < 112 and d.meta["touches"] >= 2
    assert df["close"].iloc[d.confirmed] > d.meta["level"]
    assert d.direction == "bullish"


def test_bos_and_choch_on_uptrend_then_reversal():
    wp = [(0, 100), (30, 100), (50, 112), (65, 105), (85, 120), (100, 112), (120, 128), (135, 115), (150, 104), (165, 96)]
    df = path_to_ohlc(wp, seed=2)
    dets = analyze(df, categories=["structure"]).detections
    names = [d.pattern for d in dets]
    assert "bos_bullish" in names                      # higher highs in an uptrend
    choch = [d for d in dets if d.pattern == "choch_bearish"]
    assert choch and choch[0].meta["state_before"] == "uptrend"   # first break of a higher low
    first_bos = next(d for d in dets if d.pattern == "bos_bullish")
    assert first_bos.confirmed < choch[0].confirmed


def test_trend_state_from_pivots():
    from patterniq.models import Pivot

    up = [Pivot(1, 10, "L", 3), Pivot(5, 20, "H", 7), Pivot(9, 12, "L", 11), Pivot(13, 25, "H", 15)]
    down = [Pivot(1, 30, "H", 3), Pivot(5, 20, "L", 7), Pivot(9, 28, "H", 11), Pivot(13, 15, "L", 15)]
    assert trend_state(up) == "uptrend" and trend_state(down) == "downtrend"
    assert trend_state(up[:2]) == "range"
    assert current_trend(up)["invalidation"] == 12


def test_candle_pattern_at_level_gets_confluence_bonus(demo_15m):
    dets = [d for d in analyze(demo_15m, categories=["candlestick"]).detections if d.direction != "neutral"]
    at = [d for d in dets if "at_level" in d.meta]
    away = [d for d in dets if "at_level" not in d.meta]
    assert at and away
    assert np.mean([d.quality for d in at]) > np.mean([d.quality for d in away])
