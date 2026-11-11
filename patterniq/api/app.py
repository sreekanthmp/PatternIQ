"""HTTP API (FastAPI). Serves the built web UI too when ``web/dist`` exists."""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from ..analysis import ALL_CATEGORIES, analyze
from ..config import Settings
from ..data import DataSource, make_source
from ..patterns.catalog import CATALOG
from ..serialize import _clean, analysis_payload
from ..stats import DEFAULT_HORIZONS, scorecard

WARMUP_BARS = 400  # extra history so ATR / pivots / levels are settled at the left edge
MAX_BARS = 5000


def _parse_local(value: str | None, display_tz: str, end_of_day: bool = False) -> pd.Timestamp | None:
    if not value:
        return None
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        raise HTTPException(400, f"cannot parse time {value!r}") from None
    if ts.tzinfo is not None:
        ts = ts.tz_convert(display_tz).tz_localize(None)
    if end_of_day and len(value.strip()) <= 10:
        ts = ts + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    return ts


class _TTLCache:
    def __init__(self, ttl: float, maxsize: int = 32):
        self.ttl, self.maxsize = ttl, maxsize
        self._d: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            hit = self._d.get(key)
            if hit and time.monotonic() - hit[0] < self.ttl:
                self._d.move_to_end(key)
                return hit[1]
        return None

    def put(self, key, value):
        with self._lock:
            self._d[key] = (time.monotonic(), value)
            self._d.move_to_end(key)
            while len(self._d) > self.maxsize:
                self._d.popitem(last=False)
        return value


def create_app(settings: Settings | None = None, source: DataSource | None = None) -> FastAPI:
    cfg = settings or Settings.load()
    src = source or make_source(cfg)
    app = FastAPI(title="PatternIQ", version="0.1.0")
    bounds_cache = _TTLCache(ttl=30)
    stats_cache = _TTLCache(ttl=300, maxsize=8)

    def guarded(fn):
        try:
            return fn()
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'\"")) from None
        except SQLAlchemyError as e:  # never echo the DB URL / credentials
            raise HTTPException(502, f"database error: {type(e).__name__}") from None

    def tf_or_default(tf: str | None) -> str:
        tfs = guarded(src.timeframes)
        if not tfs:
            raise HTTPException(404, "no timeframes available from the data source")
        if tf is None:
            return "15m" if "15m" in tfs else tfs[0]
        if tf not in tfs:
            raise HTTPException(404, f"timeframe {tf!r} not available (have: {', '.join(tfs)})")
        return tf

    @app.get("/api/health")
    def health():
        return {"ok": True, "source": src.kind}

    @app.get("/api/meta")
    def meta():
        def build():
            tfs = []
            for tf in src.timeframes():
                b = src.bounds(tf)
                if b:
                    tfs.append(
                        {"timeframe": tf, "first": str(b.first), "last": str(b.last), "count": b.count}
                    )
            return tfs

        tfs = bounds_cache.get("meta") or bounds_cache.put("meta", guarded(build))
        return {
            "symbol": cfg.symbol,
            "source": src.kind,
            "display_tz": cfg.display_tz,
            "timeframes": tfs,
            "categories": list(ALL_CATEGORIES),
            "patterns": [p.__dict__ for p in CATALOG.values()],
        }

    @app.get("/api/analysis")
    def analysis(
        timeframe: str | None = None,
        end: str | None = Query(None, description="Last bar to show (local time, ISO). Default: latest."),
        start: str | None = Query(None, description="First bar to show (local time, ISO); alternative to `end`."),
        bars: int = Query(600, ge=50, le=MAX_BARS),
        min_quality: float = Query(0.0, ge=0.0, le=1.0),
        categories: str | None = Query(None, description="Comma separated subset of categories."),
        include_forming: bool = True,
    ):
        tf = tf_or_default(timeframe)
        cats = [c for c in (categories or "").split(",") if c] or None
        if cats and not set(cats) <= set(ALL_CATEGORIES):
            raise HTTPException(400, f"unknown category in {cats}")
        if start and end:
            raise HTTPException(400, "pass either `start` or `end`, not both")
        end_ts = _parse_local(end, cfg.display_tz, end_of_day=True)
        start_ts = _parse_local(start, cfg.display_tz)

        if start_ts is not None:
            fwd = guarded(lambda: src.load(tf, start=start_ts, limit=bars))
            if fwd.empty:
                raise HTTPException(404, "no bars at or after that time")
            warm = guarded(
                lambda: src.load(tf, end=fwd.index[0] - pd.Timedelta(microseconds=1), limit=WARMUP_BARS)
            )
            df = pd.concat([warm, fwd])
            first_visible = len(warm)
        else:
            df = guarded(lambda: src.load(tf, end=end_ts, limit=bars + WARMUP_BARS))
            first_visible = max(0, len(df) - bars)
        if len(df) < 30:
            raise HTTPException(404, "not enough bars for this timeframe / range")
        a = analyze(df, categories=cats)
        payload = analysis_payload(
            df, a, first_visible, min_quality=min_quality, include_forming=include_forming
        )
        b = guarded(lambda: src.bounds(tf))
        payload["meta"] = {
            "symbol": cfg.symbol,
            "timeframe": tf,
            "source": src.kind,
            "bars": len(df) - first_visible,
            "first": str(df.index[first_visible]),
            "last": str(df.index[-1]),
            "has_earlier": bool(b and b.first < df.index[first_visible]),
            "has_later": bool(b and b.last > df.index[-1]),
            "data_first": str(b.first) if b else None,
            "data_last": str(b.last) if b else None,
        }
        return payload

    @app.get("/api/stats")
    def stats(
        timeframe: str | None = None,
        bars: int = Query(20000, ge=500, le=100000),
        horizons: str = "5,10,20",
        min_n: int = Query(5, ge=1),
    ):
        tf = tf_or_default(timeframe)
        try:
            hz = tuple(sorted({int(h) for h in horizons.split(",") if h.strip()}))
        except ValueError:
            raise HTTPException(400, "horizons must be comma separated integers") from None
        if not hz or any(h < 1 or h > 500 for h in hz):
            raise HTTPException(400, "horizons must be between 1 and 500 bars")

        df = guarded(lambda: src.load(tf, limit=bars))
        key = (tf, bars, hz, min_n, str(df.index[-1]) if len(df) else "")
        cached = stats_cache.get(key)
        if cached is not None:
            return cached
        a = analyze(df)
        table = scorecard(df, a.detections, a.atr, hz or DEFAULT_HORIZONS, min_n=min_n)
        rows = []
        for r in table.to_dict("records"):
            info = CATALOG.get(r["pattern"])
            rows.append({**r, "label": info.label if info else r["pattern"], "category": info.category if info else ""})
        out = _clean(
            {
                "timeframe": tf,
                "bars": len(df),
                "first": str(df.index[0]),
                "last": str(df.index[-1]),
                "horizons": list(hz),
                "rows": rows,
            }
        )
        return stats_cache.put(key, out)

    web_dir = Path(os.environ.get("PATTERNIQ_WEB_DIR") or Path(__file__).resolve().parents[2] / "web" / "dist")
    if (web_dir / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=web_dir / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "not found")
            candidate = (web_dir / path).resolve()
            if path and candidate.is_file() and web_dir.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(web_dir / "index.html")

    return app


def app_factory() -> FastAPI:  # uvicorn --factory target
    return create_app()
