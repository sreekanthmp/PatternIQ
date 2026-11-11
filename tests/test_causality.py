"""The central guarantee: what the chart shows at bar t never depends on bars after t."""

import pytest

from patterniq import analyze


def key(d):
    return (d.pattern, d.start, d.end, d.confirmed, d.direction, round(d.quality, 3))


@pytest.mark.parametrize("t", [900, 1300, 1700])
def test_detections_confirmed_by_t_are_identical_when_data_is_cut_at_t(demo_15m, t):
    df = demo_15m.iloc[:1800]
    full = {key(d) for d in analyze(df).detections if d.status == "confirmed" and d.confirmed <= t}
    cut = {key(d) for d in analyze(df.iloc[: t + 1]).detections if d.status == "confirmed" and d.confirmed <= t}
    assert len(full) > 20
    assert full == cut


def test_every_detection_is_confirmed_no_earlier_than_it_ends(demo_15m):
    for d in analyze(demo_15m).detections:
        assert d.start <= d.end <= d.confirmed or d.status == "forming"
