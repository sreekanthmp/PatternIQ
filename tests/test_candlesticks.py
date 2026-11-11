import pytest

from conftest import flat, lead
from patterniq.core.indicators import atr
from patterniq.data.synthetic import candles_to_df
from patterniq.patterns.candlestick import detect_candlesticks


def found(rows):
    df = candles_to_df(rows)
    dets = detect_candlesticks(df, atr(df))
    last = len(df) - 1
    return {d.pattern: d for d in dets if d.confirmed == last}


def test_bullish_engulfing_after_decline():
    got = found(lead("down") + [(99.9, 101.4, 99.8, 101.3)])
    assert "bullish_engulfing" in got
    d = got["bullish_engulfing"]
    assert d.direction == "bullish" and d.start == d.end - 1 and d.confirmed == d.end


def test_engulfing_needs_prior_trend():
    rows = flat() + [(101.0, 101.1, 99.9, 100.0), (99.9, 101.4, 99.8, 101.3)]  # bearish bar, then engulfing bar
    assert "bullish_engulfing" not in found(rows)


def test_bearish_engulfing_after_rally():
    got = found(lead("up") + [(100.9, 101.0, 98.6, 98.7)])
    assert got["bearish_engulfing"].direction == "bearish"


def test_hammer_vs_hanging_man_depends_on_trend():
    hammer = (98.0, 98.5, 96.0, 98.4)
    assert "hammer" in found(lead("down", end=98.0) + [hammer])
    assert "hanging_man" in found(lead("up", end=98.0) + [hammer])
    assert "hanging_man" not in found(lead("down", end=98.0) + [hammer])


def test_shooting_star_after_rally():
    got = found(lead("up", end=102.0) + [(102.0, 104.0, 101.5, 101.6)])
    assert got["shooting_star"].direction == "bearish"


def test_morning_and_evening_star():
    ms = found(lead("down") + [(100.0, 100.1, 96.9, 97.0), (96.8, 97.0, 96.7, 96.9), (97.0, 99.6, 96.9, 99.5)])
    assert ms["morning_star"].start == ms["morning_star"].end - 2
    es = found(lead("up") + [(100.0, 103.1, 99.9, 103.0), (103.2, 103.4, 103.1, 103.3), (103.0, 103.1, 100.4, 100.5)])
    assert "evening_star" in es


def test_three_white_soldiers():
    rows = flat(20) + [(100.0, 101.6, 99.9, 101.5), (100.8, 102.9, 100.7, 102.8), (102.0, 104.1, 101.9, 104.0)]
    assert "three_white_soldiers" in found(rows)


def test_doji_is_neutral_and_needs_size():
    big_doji = found(flat() + [(100.0, 101.5, 98.5, 100.05)])
    assert big_doji["doji"].direction == "neutral"
    tiny = found(flat() + [(100.0, 100.05, 99.95, 100.0)])
    assert "doji" not in tiny  # far smaller than ATR: noise, not a pattern


def test_no_detection_before_atr_exists():
    df = candles_to_df(lead("down", n=8) + [(92.9, 100.4, 92.8, 100.3)])
    assert detect_candlesticks(df, atr(df)) == []


@pytest.mark.parametrize("n", [5, 10, 40])
def test_zero_range_frames_produce_nothing_and_do_not_crash(n):
    df = candles_to_df([(100, 100, 100, 100)] * n)  # e.g. a halted instrument
    assert detect_candlesticks(df, atr(df)) == []
