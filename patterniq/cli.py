"""Command line interface: ``patterniq serve | detect | stats | inspect-db``."""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from .analysis import ALL_CATEGORIES, analyze
from .config import TIMEFRAMES, Settings
from .data import make_source, read_ohlcv_csv
from .patterns.catalog import CATALOG
from .stats import scorecard


def _load(args, settings: Settings) -> pd.DataFrame:
    if args.file:
        df = read_ohlcv_csv(args.file, settings.stored_tz, settings.display_tz)
        if args.since:
            df = df.loc[pd.Timestamp(args.since) :]
        return df.tail(args.bars) if args.bars else df
    src = make_source(settings)
    if args.since:
        return src.load(args.tf, start=pd.Timestamp(args.since), limit=args.bars)
    return src.load(args.tf, limit=args.bars)


def cmd_detect(args, settings: Settings) -> int:
    df = _load(args, settings)
    if len(df) < 30:
        print("not enough bars", file=sys.stderr)
        return 1
    a = analyze(df, categories=args.category or None)
    rows = []
    for d in a.detections:
        if d.quality < args.min_quality or (args.direction and d.direction != args.direction):
            continue
        info = CATALOG.get(d.pattern)
        rows.append(
            {
                "time": df.index[d.confirmed],
                "pattern": info.label if info else d.pattern,
                "category": d.category,
                "direction": d.direction,
                "status": d.status,
                "quality": d.quality,
                "price": round(d.price, 2),
                "notes": ", ".join(
                    f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}"
                    for k, v in d.meta.items()
                    if k in ("neckline", "target", "stop", "level", "at_level")
                ),
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        print("no detections")
        return 0
    if args.last:
        out = out.tail(args.last)
    if args.out:
        out.to_csv(args.out, index=False)
        print(f"wrote {len(out)} detections to {args.out}")
    else:
        with pd.option_context("display.width", 200, "display.max_colwidth", 60):
            print(out.to_string(index=False))
    t = a.trend
    print(f"\n{len(df)} bars {df.index[0]} -> {df.index[-1]} | trend: {t['state']}"
          f" | last swing high {t['last_swing_high']} / low {t['last_swing_low']}")
    return 0


def cmd_stats(args, settings: Settings) -> int:
    df = _load(args, settings)
    a = analyze(df)
    table = scorecard(df, a.detections, a.atr, tuple(args.horizons), min_n=args.min_n)
    if table.empty:
        print("no patterns with enough samples")
        return 0
    table["pattern"] = table["pattern"].map(lambda p: CATALOG[p].label if p in CATALOG else p)
    for col in ("hit_rate",):
        table[col] = (table[col] * 100).round(1)
    for col in ("mean_atr", "median_atr", "edge_atr", "edge_se"):
        table[col] = table[col].round(3)
    print(f"{len(df)} bars {df.index[0]} -> {df.index[-1]}  (moves in ATR units, entry = next bar open)")
    with pd.option_context("display.width", 200):
        print(table.to_string(index=False))
    print("\nedge_atr = mean signed move minus the average over all bars in that direction;")
    print("edge_se is its standard error: an edge within ~2x edge_se of zero is noise.")
    print("Signals cluster and samples are small: treat this as a sanity check, not a backtest.")
    return 0


def cmd_inspect(args, settings: Settings) -> int:
    import sqlalchemy as sa

    from .data.introspect import inspect_database, suggest_config
    from .data.sql import normalize_db_url

    if not settings.db_url:
        print("set PATTERNIQ_DB_URL (e.g. postgresql://user:pass@host:5432/prism)", file=sys.stderr)
        return 1
    engine = sa.create_engine(normalize_db_url(settings.db_url))
    schema = args.schema or settings.db_schema
    tables = inspect_database(engine, schema)
    print(f"{len(tables)} tables" + (f" in schema {schema!r}" if schema else ""))
    for t in tables:
        flag = "OHLC " if t.ohlc else "     "
        extra = ""
        if t.ohlc:
            extra = f"  rows={t.rows} {t.first} -> {t.last}" + (f"  tf~{t.timeframe_guess}" if t.timeframe_guess else "")
        print(f"  {flag}{t.name}{extra}")
        if args.columns or t.ohlc:
            print("        " + ", ".join(f"{c}:{ty}" for c, ty in t.columns.items()))
        if t.timeframe_values:
            print(f"        {t.timeframe_column} values: {', '.join(t.timeframe_values)}")
        if t.symbol_values:
            print(f"        {t.symbol_column} values: {', '.join(t.symbol_values)}")
        for note in t.notes:
            print(f"        note: {note}")
    print("\n# ---- suggested patterniq.toml ----")
    print(suggest_config(tables, schema))
    return 0


def cmd_serve(args, settings: Settings) -> int:
    import uvicorn

    from .api import create_app

    app = create_app(settings)
    print(f"PatternIQ on http://{args.host}:{args.port}  (data source: {settings.source})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="patterniq", description="Pattern recogniser for manual trading")
    p.add_argument("--config", help="path to patterniq.toml (default: ./patterniq.toml or $PATTERNIQ_CONFIG)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def data_args(sp):
        sp.add_argument("--tf", default="15m", choices=TIMEFRAMES, help="timeframe (default 15m)")
        sp.add_argument("--bars", type=int, default=1000, help="number of most recent bars (default 1000)")
        sp.add_argument("--since", help="start date/time (local), e.g. 2025-03-01")
        sp.add_argument("--file", help="analyse this OHLCV CSV instead of the configured source")

    sp = sub.add_parser("detect", help="list detected patterns")
    data_args(sp)
    sp.add_argument("--min-quality", type=float, default=0.6)
    sp.add_argument("--category", action="append", choices=ALL_CATEGORIES)
    sp.add_argument("--direction", choices=["bullish", "bearish", "neutral"])
    sp.add_argument("--last", type=int, default=40, help="show only the last N detections (0 = all)")
    sp.add_argument("--out", help="write detections to this CSV")
    sp.set_defaults(fn=cmd_detect)

    sp = sub.add_parser("stats", help="forward-return scorecard per pattern")
    data_args(sp)
    sp.set_defaults(bars=20000)
    sp.add_argument("--horizons", type=int, nargs="+", default=[5, 10, 20])
    sp.add_argument("--min-n", type=int, default=10)
    sp.set_defaults(fn=cmd_stats)

    sp = sub.add_parser("inspect-db", help="list tables in the database and suggest a config")
    sp.add_argument("--schema")
    sp.add_argument("--columns", action="store_true", help="print columns of every table")
    sp.set_defaults(fn=cmd_inspect)

    sp = sub.add_parser("serve", help="run the API + web UI")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8000)
    sp.set_defaults(fn=cmd_serve)

    args = p.parse_args(argv)
    settings = Settings.load(args.config)
    return args.fn(args, settings)


if __name__ == "__main__":
    raise SystemExit(main())
