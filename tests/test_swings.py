import numpy as np

from patterniq.core.indicators import atr
from patterniq.core.swings import label_swings, zigzag
from patterniq.data.synthetic import path_to_ohlc


def pivots_of(df, mult=3.0):
    return zigzag(df["high"].to_numpy(), df["low"].to_numpy(), atr(df), mult)


def test_pivots_alternate_and_are_confirmed_after_they_happen():
    df = path_to_ohlc([(0, 100), (30, 100), (50, 120), (70, 105), (90, 125), (110, 100)], seed=3)
    piv = pivots_of(df)
    assert len(piv) >= 3
    assert all(a.kind != b.kind for a, b in zip(piv, piv[1:]))
    assert all(p.confirmed > p.idx for p in piv)
    highs = [p for p in piv if p.kind == "H"]
    assert any(abs(p.idx - 50) <= 2 for p in highs) and any(abs(p.idx - 90) <= 2 for p in highs)


def test_zigzag_never_repaints():
    df = path_to_ohlc([(0, 100), (40, 100), (60, 130), (80, 110), (100, 135), (120, 105), (140, 140)], seed=5)
    full = pivots_of(df)
    for t in (70, 95, 115, 135):
        part = zigzag(df["high"].to_numpy()[: t + 1], df["low"].to_numpy()[: t + 1], atr(df.iloc[: t + 1]), 3.0)
        assert part == [p for p in full if p.confirmed <= t]


def test_swing_labels():
    df = path_to_ohlc([(0, 100), (30, 100), (50, 120), (70, 108), (90, 130), (110, 112), (130, 150)], seed=2)
    labels = [lab for _, lab in label_swings(pivots_of(df)) if lab]
    assert "HH" in labels and "HL" in labels
    assert "LH" not in labels and "LL" not in labels


def test_constant_prices_have_no_pivots_and_do_not_crash():
    df = path_to_ohlc([(0, 100), (200, 100)], seed=1, wick=0.0, jitter=0.0)
    assert pivots_of(df) == []
