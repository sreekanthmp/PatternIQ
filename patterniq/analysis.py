"""Single entry point: run every detector over an OHLC frame."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .core.indicators import atr as compute_atr
from .core.swings import label_swings, zigzag
from .models import (
    BEARISH,
    BULLISH,
    CATEGORY_CANDLE,
    CATEGORY_CHART,
    CATEGORY_LEVEL,
    CATEGORY_STRUCTURE,
    Detection,
    Level,
    Pivot,
)
from .patterns.candlestick import CandleParams, detect_candlesticks
from .patterns.chart import ChartParams, detect_chart_patterns
from .patterns.levels import LevelParams, LevelTimeline, detect_level_events, reference_levels
from .patterns.structure import current_trend, detect_structure_breaks

ALL_CATEGORIES = (CATEGORY_CANDLE, CATEGORY_CHART, CATEGORY_LEVEL, CATEGORY_STRUCTURE)


@dataclass
class AnalysisConfig:
    atr_period: int = 14
    structure_scale: float = 2.0  # zigzag size (ATR) for trend structure (HH/HL, BOS, CHoCH)
    level_scale: float = 3.0  # zigzag size (ATR) for support/resistance pivots (fewer, stronger levels)
    candle: CandleParams = field(default_factory=CandleParams)
    chart: ChartParams = field(default_factory=ChartParams)
    levels: LevelParams = field(default_factory=LevelParams)
    confluence_atr: float = 0.5  # candle extreme this close to a level counts as "at level"
    confluence_bonus: float = 0.25


@dataclass
class Analysis:
    detections: list[Detection]
    pivots: list[Pivot]
    swing_labels: list[tuple[Pivot, str]]
    levels: list[Level]
    trend: dict
    reference: dict
    atr: np.ndarray


def _validate(df: pd.DataFrame) -> None:
    missing = {"open", "high", "low", "close"} - set(df.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")


def _add_confluence(
    dets: list[Detection], df: pd.DataFrame, atr: np.ndarray, timeline: LevelTimeline, cfg: AnalysisConfig
) -> None:
    """Flag candlestick patterns that form at a known support/resistance level."""
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    for d in dets:
        if d.category != CATEGORY_CANDLE or d.direction not in (BULLISH, BEARISH):
            continue
        levels = timeline.at(d.start - 1) if d.start > 0 else []
        if not levels:
            continue
        a = atr[d.confirmed]
        if d.direction == BULLISH:
            extreme = float(low[d.start : d.end + 1].min())
        else:
            extreme = float(high[d.start : d.end + 1].max())
        best = min(levels, key=lambda lv: abs(lv.price - extreme))
        if abs(best.price - extreme) <= cfg.confluence_atr * a:
            d.meta["at_level"] = round(best.price, 2)
            d.quality = round(min(1.0, d.quality + cfg.confluence_bonus), 3)


def analyze(
    df: pd.DataFrame,
    config: AnalysisConfig | None = None,
    categories: tuple[str, ...] | list[str] | None = None,
) -> Analysis:
    """Detect patterns in ``df`` (columns open/high/low/close, ascending time index)."""
    _validate(df)
    cfg = config or AnalysisConfig()
    cats = set(categories) if categories else set(ALL_CATEGORIES)
    n = len(df)
    a = compute_atr(df, cfg.atr_period)

    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    pivots = zigzag(high, low, a, cfg.structure_scale) if n > cfg.atr_period else []
    level_pivots = zigzag(high, low, a, cfg.level_scale) if n > cfg.atr_period else []
    timeline = LevelTimeline(level_pivots, a, cfg.levels)

    dets: list[Detection] = []
    if CATEGORY_CANDLE in cats:
        candles = detect_candlesticks(df, a, cfg.candle)
        _add_confluence(candles, df, a, timeline, cfg)
        dets += candles
    if CATEGORY_CHART in cats:
        dets += detect_chart_patterns(df, a, cfg.chart)
    if CATEGORY_LEVEL in cats:
        dets += detect_level_events(df, a, timeline)
    if CATEGORY_STRUCTURE in cats:
        dets += detect_structure_breaks(df, a, pivots)
    dets.sort(key=lambda d: (d.confirmed, d.category, d.pattern))

    last_close = float(df["close"].iloc[-1]) if n else None
    levels = timeline.at(n - 1) if n else []
    intraday = n > 1 and (df.index[1] - df.index[0]) < pd.Timedelta(hours=12)
    return Analysis(
        detections=dets,
        pivots=pivots,
        swing_labels=label_swings(pivots),
        levels=sorted(levels, key=lambda lv: lv.price),
        trend=current_trend(pivots, last_close),
        reference=reference_levels(df) if intraday else {},
        atr=a,
    )
