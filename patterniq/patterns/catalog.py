"""Human-readable metadata for every pattern the library can emit."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import (
    BEARISH,
    BULLISH,
    CATEGORY_CANDLE,
    CATEGORY_CHART,
    CATEGORY_LEVEL,
    CATEGORY_STRUCTURE,
    NEUTRAL,
)


@dataclass(frozen=True)
class PatternInfo:
    name: str
    label: str
    category: str
    direction: str  # bullish | bearish | neutral | dynamic (decided by the breakout side)
    description: str


def _p(name, label, category, direction, description) -> PatternInfo:
    return PatternInfo(name, label, category, direction, description)


_C, _X, _L, _S = CATEGORY_CANDLE, CATEGORY_CHART, CATEGORY_LEVEL, CATEGORY_STRUCTURE

_ALL = [
    # ---- candlesticks: single bar --------------------------------------------------
    _p("doji", "Doji", _C, NEUTRAL, "Open and close almost equal: indecision."),
    _p("dragonfly_doji", "Dragonfly Doji", _C, BULLISH, "Doji with a long lower shadow after a decline."),
    _p("gravestone_doji", "Gravestone Doji", _C, BEARISH, "Doji with a long upper shadow after a rally."),
    _p("hammer", "Hammer", _C, BULLISH, "Small body near the high, long lower shadow, after a decline."),
    _p("hanging_man", "Hanging Man", _C, BEARISH, "Hammer shape after a rally; warns of weakening buyers."),
    _p("inverted_hammer", "Inverted Hammer", _C, BULLISH, "Small body near the low, long upper shadow, after a decline."),
    _p("shooting_star", "Shooting Star", _C, BEARISH, "Small body near the low, long upper shadow, after a rally."),
    _p("bullish_marubozu", "Bullish Marubozu", _C, BULLISH, "Large bullish bar with almost no shadows: strong buying."),
    _p("bearish_marubozu", "Bearish Marubozu", _C, BEARISH, "Large bearish bar with almost no shadows: strong selling."),
    # ---- candlesticks: two bars ----------------------------------------------------
    _p("bullish_engulfing", "Bullish Engulfing", _C, BULLISH, "Bullish bar whose body engulfs the prior bearish body, after a decline."),
    _p("bearish_engulfing", "Bearish Engulfing", _C, BEARISH, "Bearish bar whose body engulfs the prior bullish body, after a rally."),
    _p("piercing_line", "Piercing Line", _C, BULLISH, "Bullish bar closing above the midpoint of a long bearish bar, after a decline."),
    _p("dark_cloud_cover", "Dark Cloud Cover", _C, BEARISH, "Bearish bar closing below the midpoint of a long bullish bar, after a rally."),
    _p("bullish_harami", "Bullish Harami", _C, BULLISH, "Small bullish body inside a long bearish body, after a decline."),
    _p("bearish_harami", "Bearish Harami", _C, BEARISH, "Small bearish body inside a long bullish body, after a rally."),
    _p("tweezer_bottom", "Tweezer Bottom", _C, BULLISH, "Two bars with matching lows (bearish then bullish), after a decline."),
    _p("tweezer_top", "Tweezer Top", _C, BEARISH, "Two bars with matching highs (bullish then bearish), after a rally."),
    # ---- candlesticks: three bars --------------------------------------------------
    _p("morning_star", "Morning Star", _C, BULLISH, "Long bearish bar, small indecision bar, strong bullish bar."),
    _p("evening_star", "Evening Star", _C, BEARISH, "Long bullish bar, small indecision bar, strong bearish bar."),
    _p("three_white_soldiers", "Three White Soldiers", _C, BULLISH, "Three consecutive strong bullish bars, each closing higher."),
    _p("three_black_crows", "Three Black Crows", _C, BEARISH, "Three consecutive strong bearish bars, each closing lower."),
    # ---- chart patterns ------------------------------------------------------------
    _p("double_top", "Double Top", _X, BEARISH, "Two similar peaks; confirmed when price closes below the middle trough (neckline)."),
    _p("double_bottom", "Double Bottom", _X, BULLISH, "Two similar troughs; confirmed when price closes above the middle peak (neckline)."),
    _p("head_and_shoulders", "Head & Shoulders", _X, BEARISH, "Three peaks, the middle one highest; confirmed by a close below the neckline."),
    _p("inverse_head_and_shoulders", "Inverse Head & Shoulders", _X, BULLISH, "Three troughs, the middle one lowest; confirmed by a close above the neckline."),
    _p("bull_flag", "Bull Flag", _X, BULLISH, "Sharp rally (pole) then a shallow downward drift; confirmed by a break above the flag."),
    _p("bear_flag", "Bear Flag", _X, BEARISH, "Sharp decline (pole) then a shallow upward drift; confirmed by a break below the flag."),
    _p("ascending_triangle", "Ascending Triangle", _X, "dynamic", "Flat resistance with rising lows. Usually resolves upward; direction is the actual breakout."),
    _p("descending_triangle", "Descending Triangle", _X, "dynamic", "Flat support with falling highs. Usually resolves downward; direction is the actual breakout."),
    _p("symmetrical_triangle", "Symmetrical Triangle", _X, "dynamic", "Converging falling highs and rising lows; direction is the breakout side."),
    _p("rising_wedge", "Rising Wedge", _X, "dynamic", "Both lines rising and converging. Usually resolves downward."),
    _p("falling_wedge", "Falling Wedge", _X, "dynamic", "Both lines falling and converging. Usually resolves upward."),
    _p("rectangle", "Rectangle / Range", _X, "dynamic", "Price oscillating between flat support and resistance; direction is the breakout side."),
    _p("ascending_channel", "Ascending Channel", _X, "dynamic", "Parallel rising lines; direction is the breakout side."),
    _p("descending_channel", "Descending Channel", _X, "dynamic", "Parallel falling lines; direction is the breakout side."),
    # ---- support / resistance ------------------------------------------------------
    _p("resistance_breakout", "Resistance Breakout", _L, BULLISH, "Close above a level that was tested at least twice as resistance."),
    _p("support_breakdown", "Support Breakdown", _L, BEARISH, "Close below a level that was tested at least twice as support."),
    _p("resistance_rejection", "Resistance Rejection", _L, BEARISH, "Bar spikes into resistance and closes back below it with an upper wick."),
    _p("support_rejection", "Support Rejection", _L, BULLISH, "Bar spikes into support and closes back above it with a lower wick."),
    # ---- trend structure -----------------------------------------------------------
    _p("bos_bullish", "Break of Structure (up)", _S, BULLISH, "Close above the last swing high while the trend was up or ranging: trend continuation."),
    _p("bos_bearish", "Break of Structure (down)", _S, BEARISH, "Close below the last swing low while the trend was down or ranging: trend continuation."),
    _p("choch_bullish", "Change of Character (up)", _S, BULLISH, "Close above the last lower-high in a downtrend: first sign the trend may be turning."),
    _p("choch_bearish", "Change of Character (down)", _S, BEARISH, "Close below the last higher-low in an uptrend: first sign the trend may be turning."),
]

CATALOG: dict[str, PatternInfo] = {p.name: p for p in _ALL}
CATEGORIES = [CATEGORY_CANDLE, CATEGORY_CHART, CATEGORY_LEVEL, CATEGORY_STRUCTURE]


def info(name: str) -> PatternInfo:
    return CATALOG[name]
