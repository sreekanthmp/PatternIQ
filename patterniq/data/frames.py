"""In-memory sources: CSV files and generated demo data."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from ..config import TF_MINUTES, TIMEFRAMES, Settings
from .base import Bounds, clean_ohlc, to_local_naive
from .synthetic import generate_demo_1m, resample_ohlc

_TS_NAMES = ("timestamp", "datetime", "date_time", "date", "time", "ts")


def read_ohlcv_csv(path: str | Path, stored_tz: str = "Asia/Kolkata", display_tz: str = "Asia/Kolkata") -> pd.DataFrame:
    """Read an OHLCV CSV with flexible, case-insensitive headers (TradingView / broker exports)."""
    raw = pd.read_csv(path)
    lower = {c.lower().strip(): c for c in raw.columns}
    ts_col = next((lower[n] for n in _TS_NAMES if n in lower), None)
    if ts_col is None:
        raise ValueError(f"{path}: no timestamp column (looked for {', '.join(_TS_NAMES)})")
    try:
        cols = {k: lower[k] for k in ("open", "high", "low", "close")}
    except KeyError as e:
        raise ValueError(f"{path}: missing column {e}") from None
    df = pd.DataFrame({k: raw[v] for k, v in cols.items()})
    df["volume"] = raw[lower["volume"]] if "volume" in lower else 0.0
    df.index = to_local_naive(raw[ts_col], stored_tz, display_tz)
    return clean_ohlc(df)


class FrameSource:
    """Serves slices of pre-loaded frames; coarser timeframes are derived from finer ones."""

    kind = "frames"

    def __init__(self):
        self._frames: dict[str, pd.DataFrame] = {}

    def _load_frame(self, tf: str) -> pd.DataFrame | None:  # pragma: no cover - overridden
        raise NotImplementedError

    def frame(self, tf: str) -> pd.DataFrame | None:
        if tf not in self._frames:
            df = self._load_frame(tf)
            if df is None:  # derive from the next finer timeframe we can find
                for finer in sorted((t for t in TIMEFRAMES if TF_MINUTES[t] < TF_MINUTES[tf]), key=lambda t: -TF_MINUTES[t]):
                    base = self.frame(finer)
                    if base is not None:
                        df = resample_ohlc(base, tf)
                        break
            if df is None:
                return None
            self._frames[tf] = df
        return self._frames[tf]

    def timeframes(self) -> list[str]:
        return [tf for tf in TIMEFRAMES if self.frame(tf) is not None]

    def bounds(self, timeframe: str) -> Bounds | None:
        df = self.frame(timeframe)
        if df is None or df.empty:
            return None
        return Bounds(first=df.index[0], last=df.index[-1], count=len(df))

    def load(self, timeframe: str, *, end: datetime | None = None, limit: int | None = 500, start: datetime | None = None) -> pd.DataFrame:
        df = self.frame(timeframe)
        if df is None:
            raise KeyError(f"no data for timeframe {timeframe!r}")
        if end is not None:
            df = df.loc[: pd.Timestamp(end)]
        if start is not None:
            df = df.loc[pd.Timestamp(start) :]
            return df.head(limit) if limit else df
        return df.tail(limit) if limit else df


class DemoSource(FrameSource):
    kind = "demo"

    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings

    def _load_frame(self, tf: str) -> pd.DataFrame | None:
        if tf == "1m":
            return generate_demo_1m(days=self.s.demo_days)
        return None  # derived from 1m


class CsvSource(FrameSource):
    """Looks for ``<csv_dir>/<tf>.csv`` (e.g. ``15m.csv``); missing timeframes are derived."""

    kind = "csv"

    def __init__(self, settings: Settings):
        super().__init__()
        if not settings.csv_dir:
            raise ValueError("no CSV directory: set PATTERNIQ_CSV_DIR or csv_dir in patterniq.toml")
        self.s = settings
        self.dir = Path(settings.csv_dir)

    def _load_frame(self, tf: str) -> pd.DataFrame | None:
        path = self.dir / f"{tf}.csv"
        if not path.is_file():
            return None
        return read_ohlcv_csv(path, self.s.stored_tz, self.s.display_tz)
