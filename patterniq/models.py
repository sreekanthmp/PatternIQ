"""Plain data containers shared by every detector.

All positions are *bar indices* into the OHLC frame that was analysed. Conversion to
timestamps happens at the edge (API / CLI), so the detectors stay free of time handling.

Causality contract
------------------
Every :class:`Detection` carries a ``confirmed`` index: the first bar whose *close* is
enough to know the pattern exists. Nothing the detectors emit uses data after that bar,
so what you see on a replayed chart is what you could have seen live. ``status="forming"``
detections are the one exception by design: they describe a setup that has not resolved
yet at the right edge of the data and may disappear or turn into a confirmed detection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

BULLISH = "bullish"
BEARISH = "bearish"
NEUTRAL = "neutral"

CATEGORY_CANDLE = "candlestick"
CATEGORY_CHART = "chart"
CATEGORY_LEVEL = "level"
CATEGORY_STRUCTURE = "structure"

CONFIRMED = "confirmed"
FORMING = "forming"


@dataclass(frozen=True)
class Pivot:
    """A swing high ("H") or swing low ("L").

    ``confirmed`` is the bar at which the reversal was large enough for the pivot to be
    known. ``idx`` is where the extreme actually happened (always <= ``confirmed``).
    """

    idx: int
    price: float
    kind: str
    confirmed: int


@dataclass
class Line:
    """A straight segment to draw on the chart (neckline, trendline, level, ...)."""

    i1: int
    p1: float
    i2: int
    p2: float
    role: str = "line"

    def value_at(self, i: float) -> float:
        if self.i2 == self.i1:
            return self.p1
        return self.p1 + (self.p2 - self.p1) * (i - self.i1) / (self.i2 - self.i1)


@dataclass
class Detection:
    pattern: str
    category: str
    direction: str
    start: int
    end: int
    confirmed: int
    quality: float
    price: float
    status: str = CONFIRMED
    points: list[tuple[int, float, str]] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def span(self) -> tuple[int, int]:
        return self.start, max(self.end, self.confirmed)


@dataclass
class Level:
    """A horizontal support/resistance zone built from clustered swing pivots."""

    price: float
    touches: int
    first_idx: int
    last_idx: int
    valid_from: int
    strength: float

    def kind_vs(self, price: float) -> str:
        return "support" if self.price <= price else "resistance"
