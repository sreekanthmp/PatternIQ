import pytest

from patterniq import analyze
from patterniq.data.synthetic import path_to_ohlc

LEAD = [(0, 100), (40, 100)]

CASES = {
    "double_top": (LEAD + [(60, 112), (80, 102), (100, 112), (125, 92), (140, 90)], "bearish"),
    "double_bottom": (LEAD + [(60, 88), (80, 98), (100, 88), (125, 108), (140, 110)], "bullish"),
    "head_and_shoulders": (LEAD + [(60, 110), (75, 100), (95, 118), (115, 100), (135, 110), (160, 88), (170, 86)], "bearish"),
    "inverse_head_and_shoulders": (LEAD + [(60, 90), (75, 100), (95, 82), (115, 100), (135, 90), (160, 112), (170, 114)], "bullish"),
    "bull_flag": (LEAD + [(55, 130), (70, 122), (80, 126), (90, 119), (100, 123), (110, 116), (125, 140), (135, 145)], "bullish"),
    "bear_flag": (LEAD + [(55, 70), (70, 78), (80, 74), (90, 81), (100, 77), (110, 84), (125, 60), (135, 55)], "bearish"),
    "ascending_triangle": (LEAD + [(60, 110), (80, 100), (100, 110), (115, 104), (130, 110), (142, 107), (150, 110), (165, 125), (175, 128)], "bullish"),
    "symmetrical_triangle": (LEAD + [(60, 120), (80, 90), (100, 114), (115, 96), (130, 108), (142, 100), (150, 105), (170, 125), (180, 128)], "bullish"),
}


def chart(df):
    return [d for d in analyze(df, categories=["chart"]).detections if d.status == "confirmed"]


@pytest.mark.parametrize("name", CASES)
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_textbook_formation_is_found_once_with_correct_direction(name, seed):
    waypoints, direction = CASES[name]
    dets = chart(path_to_ohlc(waypoints, seed=seed))
    assert [d.pattern for d in dets] == [name], dets
    assert dets[0].direction == direction


@pytest.mark.parametrize("name", ["double_top", "double_bottom", "head_and_shoulders", "inverse_head_and_shoulders"])
def test_reversal_patterns_report_neckline_target_and_stop(name):
    d = chart(path_to_ohlc(CASES[name][0], seed=1))[0]
    assert {"neckline", "target", "stop"} <= set(d.meta)
    bullish = d.direction == "bullish"
    # target lies beyond the neckline in the trade direction; the stop on the other side
    assert (d.meta["target"] > d.meta["neckline"]) == bullish
    assert (d.meta["stop"] < d.meta["neckline"]) == bullish
    assert any(l.role == "neckline" for l in d.lines)


def test_mirrored_price_series_gives_mirrored_pattern():
    wp, _ = CASES["double_top"]
    df = path_to_ohlc(wp, seed=4)
    flipped = df.copy()
    flipped["open"], flipped["close"] = 200 - df["open"], 200 - df["close"]
    flipped["high"], flipped["low"] = 200 - df["low"], 200 - df["high"]
    a, b = chart(df), chart(flipped)
    assert [d.pattern for d in a] == ["double_top"] and [d.pattern for d in b] == ["double_bottom"]
    assert a[0].confirmed == b[0].confirmed


def test_confirmation_is_the_breakout_bar_not_earlier():
    d = chart(path_to_ohlc(CASES["double_top"][0], seed=1))[0]
    df = path_to_ohlc(CASES["double_top"][0], seed=1)
    neck = d.meta["neckline"]
    assert df["close"].iloc[d.confirmed] < neck          # closed below the neckline on that bar ...
    assert (df["close"].iloc[d.end + 1 : d.confirmed] >= neck).all()  # ... and not before


def test_setup_is_reported_as_forming_until_it_breaks():
    waypoints = [w for w in CASES["double_top"][0] if w[0] <= 100] + [(108, 108)]
    df = path_to_ohlc(waypoints, seed=1)
    dets = [d for d in analyze(df, categories=["chart"]).detections if d.pattern == "double_top"]
    assert len(dets) == 1 and dets[0].status == "forming" and dets[0].direction == "neutral"


def test_failed_pattern_is_not_reported():
    # second top is exceeded decisively instead of rolling over -> invalid double top
    wp = LEAD + [(60, 112), (80, 102), (100, 112), (115, 122), (140, 126)]
    assert not [d for d in chart(path_to_ohlc(wp, seed=1)) if d.pattern == "double_top"]


def test_random_noise_produces_few_high_quality_patterns(demo_15m):
    dets = [d for d in analyze(demo_15m, categories=["chart"]).detections if d.quality >= 0.7]
    assert len(dets) / len(demo_15m) < 0.02
