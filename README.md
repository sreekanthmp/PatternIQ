# PatternIQ

A pattern recogniser for **manual trading**. It scans your candles (built for Nifty 50 on 1m / 5m / 15m / 1d, but nothing is hard-coded to it), marks what it finds on an interactive chart, and tells you where price sits relative to structure and key levels. You make the decisions; it does the pattern-spotting.

* **43 patterns** across candlesticks, chart patterns, support/resistance events and trend structure (list below)
* **Python engine** (pandas/numpy) + **FastAPI** + **React/TypeScript** dashboard using TradingView's open-source `lightweight-charts`
* Reads candles from **your PostgreSQL database** (any table/column layout), CSV files, or built-in demo data
* **No lookahead**: a pattern appears on the bar whose close confirms it, never earlier (verified by tests)
* A **scorecard** that shows what actually happened after each pattern *on your data*, with standard errors, so you can tell signal from noise

> Not trading advice. Pattern detection is a charting aid; the scorecard is a sanity check, not a backtest.

## Quick start (demo data, nothing to configure)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
(cd web && npm install && npm run build)
patterniq serve            # http://127.0.0.1:8000
```

With no database configured PatternIQ shows **synthetic demo data** (random, deliberately free of any edge) and says so in a banner.

## Connect your `prism` PostgreSQL database

PatternIQ reads the table layout from the database itself, so **no config file is needed** for a typical setup. It only ever **reads** (the connection is opened read-only).

1. Give it the connection string (keep it out of files). In PowerShell:

   ```powershell
   $env:PATTERNIQ_DB_URL = "postgresql://user:password@localhost:5432/prism"
   ```

   (bash: `export PATTERNIQ_DB_URL="postgresql://user:password@localhost:5432/prism"`)

2. Check what it finds:

   ```bash
   patterniq check
   ```

   It prints the table and columns it detected, the symbol it will use, and the row count per timeframe. If something is off it says why (wrong password, table not found, symbol or timeframe labels that don't match what is stored, a config file in the wrong place).

3. Run it: `patterniq serve`.

Auto-detection refuses to guess when the answer would be a coin toss (several candidate tables, or several stored symbols with none of them Nifty 50) and tells you what to set. To override anything, copy [`patterniq.example.toml`](patterniq.example.toml) to `patterniq.toml` in the folder you run `patterniq` from (it is git-ignored); `patterniq inspect-db` prints the detected layout and values as a starting point.

Detected layouts: one table per timeframe, or one table with a timeframe column (optionally also a symbol column). `inspect-db` prints the values actually stored in the `timeframe` and `symbol` columns and maps them for you (e.g. `15m = "15"`, `1d = "D"`). **If one table holds several instruments, map the `symbol` column**: without it PatternIQ refuses to load rather than silently showing a mix of markets. Timestamps can be `timestamp`, `timestamptz`, `date` or epoch seconds/milliseconds; the column type is detected automatically. **Naive timestamps are assumed to be Asia/Kolkata**; if you store UTC, set `stored_tz = "UTC"`. If you only have 1-minute data, 5m/15m/1d are derived from it automatically for CSV sources.

To analyse a TradingView or broker CSV export without any database:

```bash
patterniq detect --file nifty_15m.csv --tf 15m --min-quality 0.6
```

## Live data from Angel One (SmartAPI)

The dashboard only *reads* the database. A separate process, `patterniq live`, logs in to Angel One, pulls candles and **appends each bar to your candle table once the bar has closed**. Run both side by side.

**Set up (once)**

```bash
pip install -e ".[live]"        # the Angel SDK, plus two packages it imports but forgets to declare
```

Copy [`.env.example`](.env.example) to `.env` in the folder you run `patterniq` from (it is git-ignored) and fill it in:

```
ANGEL_API_KEY=...
ANGEL_CLIENT_ID=...
ANGEL_PASSWORD=...        # your Angel login PIN
ANGEL_TOTP=...            # the TOTP *secret* (the long key from enrolment), not the 6-digit code
PG_USER=...  PG_PASSWORD=...  PG_HOST=localhost  PG_PORT=5432  PG_DATABASE=prism     # or PATTERNIQ_DB_URL
```

Real environment variables win over `.env`. Depending on how the API key was created, Angel may need its historical-data access enabled for that key; if `live-check` reports a permission error, check the app's settings in the SmartAPI portal.

**First run: a rehearsal that writes nothing**

```bash
patterniq live-check
```

It logs in, prints the latest real 1-minute candles (so you can see the symbol token is right and prices look like Nifty), shows the newest stored bar per timeframe, confirms the database user may `INSERT`, and prints what a sync would add.

**Then**

```bash
patterniq live --dry-run      # the real loop, but nothing is written; watch the log lines
patterniq live                # keep this running during market hours (09:15-15:30 IST)
patterniq serve               # in another terminal, then switch on Live (and Alerts) in the dashboard
```

**What it writes.** `1m` bars come from Angel; `5m` and `15m` bars are built from those closed 1m bars (anchored at 09:15, emitted only when complete); the `1d` bar is Angel's daily candle, fetched after 15:30. On start it fills any gap since the newest stored bar (at most `--max-backfill-days`, default 45). Rows are **insert-only and idempotent**: existing bars are never updated or deleted, and running two copies at once is not supported. Built-in Angel tokens exist for `NIFTY` and `BANKNIFTY`; for anything else pass `--token` (and `--exchange`).

**Dashboard.** *Live* re-reads the latest bars every 15 s without moving your zoom (if you were at the right edge it follows the newest bar; if you had scrolled back it stays put). *Alerts* shows a toast, beeps, and raises a desktop notification when a **confirmed** pattern appears that was not there on the previous refresh, using your current category / quality / pattern filters. The status dot shows whether new candles are really arriving, so a stopped `patterniq live` is visible. Alerts cover the timeframe on screen and only work while the tab is open.

**Security.**
* It is read-only towards the broker: login and historical candles only. There is no order code in this repository.
* Keep `.env` out of git and out of chats. **Rotate** any credential that has been pasted anywhere (change the PIN, re-enrol the TOTP, regenerate the API key in the SmartAPI portal).
* The Angel SDK writes the full request (including the PIN, API key and the one-time code) to its error log when a call fails. PatternIQ switches that logger off and redacts its own messages; there is a test that runs the real SDK against a fake network and checks nothing leaks. If you use the SDK in your own scripts, do the same.
* On import the SDK asks `api.ipify.org` for your public IP (Angel requires it as a request header).

## Using the dashboard

* **Chart**: candles, one arrow per bar (`+n` when several patterns coincide), chart-pattern geometry (necklines, trendlines, flag poles; dashed while still forming), support/resistance lines (`R×3` = resistance tested 3 times) and previous-day high/low/close.
* **Filters**: categories, minimum quality, and a per-pattern picker, so you can keep only the setups you trade. Settings persist in your browser.
* **Signals tab**: a "market now" card (trend state from swing structure, the level that would break the trend, nearest support/resistance in points and ATR), then every pattern newest-first. Click one to jump to it; chart patterns show their neckline, measured target and invalidation level.
* **Scorecard tab**: forward moves after each pattern, in ATR units, with edge vs. drift and its standard error. Rows within two standard errors of zero are greyed.
* Click a bar to select the best pattern on it; use ◀ ▶ / **Go to** to move through history.

## What the numbers mean

* **Confirmed** = the first bar whose *close* completes the pattern (candle patterns: the last bar; chart patterns: the first close beyond the neckline/trendline; levels: the breaking close). Chart patterns that are complete but haven't triggered at the right edge are shown as **forming** and may vanish.
* **Quality (0-100%)** is a heuristic fit score, **not a probability**. Candles: strength of the prior move, bar size, and +25 points for forming at a support/resistance level. Chart patterns: symmetry/fit, size, and breakout candle strength. Levels: touches, recency, and how decisively price broke or rejected.
* **Swings** use an ATR-scaled zigzag that never repaints. Support/resistance clusters swing pivots (3x ATR) and only exists once its touches have been confirmed. Chart patterns run at two swing scales (3x and 6x ATR) so small and large formations are both found.
* **Scorecard**: enter at the open of the bar after confirmation, hold N bars, measure the move in the pattern's direction divided by ATR at the signal. *Edge* = mean move minus the same-direction average over all bars. Signals cluster and tested patterns are many, so a few rows will look significant by luck; treat it as a filter for obvious duds, not proof.

## Patterns

**Candlestick patterns**

| Pattern | Direction | What it means |
|---|---|---|
| Doji | ◆ neutral | Open and close almost equal: indecision. |
| Dragonfly Doji | ▲ bullish | Doji with a long lower shadow after a decline. |
| Gravestone Doji | ▼ bearish | Doji with a long upper shadow after a rally. |
| Hammer | ▲ bullish | Small body near the high, long lower shadow, after a decline. |
| Hanging Man | ▼ bearish | Hammer shape after a rally; warns of weakening buyers. |
| Inverted Hammer | ▲ bullish | Small body near the low, long upper shadow, after a decline. |
| Shooting Star | ▼ bearish | Small body near the low, long upper shadow, after a rally. |
| Bullish Marubozu | ▲ bullish | Large bullish bar with almost no shadows: strong buying. |
| Bearish Marubozu | ▼ bearish | Large bearish bar with almost no shadows: strong selling. |
| Bullish Engulfing | ▲ bullish | Bullish bar whose body engulfs the prior bearish body, after a decline. |
| Bearish Engulfing | ▼ bearish | Bearish bar whose body engulfs the prior bullish body, after a rally. |
| Piercing Line | ▲ bullish | Bullish bar closing above the midpoint of a long bearish bar, after a decline. |
| Dark Cloud Cover | ▼ bearish | Bearish bar closing below the midpoint of a long bullish bar, after a rally. |
| Bullish Harami | ▲ bullish | Small bullish body inside a long bearish body, after a decline. |
| Bearish Harami | ▼ bearish | Small bearish body inside a long bullish body, after a rally. |
| Tweezer Bottom | ▲ bullish | Two bars with matching lows (bearish then bullish), after a decline. |
| Tweezer Top | ▼ bearish | Two bars with matching highs (bullish then bearish), after a rally. |
| Morning Star | ▲ bullish | Long bearish bar, small indecision bar, strong bullish bar. |
| Evening Star | ▼ bearish | Long bullish bar, small indecision bar, strong bearish bar. |
| Three White Soldiers | ▲ bullish | Three consecutive strong bullish bars, each closing higher. |
| Three Black Crows | ▼ bearish | Three consecutive strong bearish bars, each closing lower. |

**Chart patterns**

| Pattern | Direction | What it means |
|---|---|---|
| Double Top | ▼ bearish | Two similar peaks; confirmed when price closes below the middle trough (neckline). |
| Double Bottom | ▲ bullish | Two similar troughs; confirmed when price closes above the middle peak (neckline). |
| Head & Shoulders | ▼ bearish | Three peaks, the middle one highest; confirmed by a close below the neckline. |
| Inverse Head & Shoulders | ▲ bullish | Three troughs, the middle one lowest; confirmed by a close above the neckline. |
| Bull Flag | ▲ bullish | Sharp rally (pole) then a shallow downward drift; confirmed by a break above the flag. |
| Bear Flag | ▼ bearish | Sharp decline (pole) then a shallow upward drift; confirmed by a break below the flag. |
| Ascending Triangle | by breakout side | Flat resistance with rising lows. Usually resolves upward; direction is the actual breakout. |
| Descending Triangle | by breakout side | Flat support with falling highs. Usually resolves downward; direction is the actual breakout. |
| Symmetrical Triangle | by breakout side | Converging falling highs and rising lows; direction is the breakout side. |
| Rising Wedge | by breakout side | Both lines rising and converging. Usually resolves downward. |
| Falling Wedge | by breakout side | Both lines falling and converging. Usually resolves upward. |
| Rectangle / Range | by breakout side | Price oscillating between flat support and resistance; direction is the breakout side. |
| Ascending Channel | by breakout side | Parallel rising lines; direction is the breakout side. |
| Descending Channel | by breakout side | Parallel falling lines; direction is the breakout side. |

**Support / resistance events**

| Pattern | Direction | What it means |
|---|---|---|
| Resistance Breakout | ▲ bullish | Close above a level that was tested at least twice as resistance. |
| Support Breakdown | ▼ bearish | Close below a level that was tested at least twice as support. |
| Resistance Rejection | ▼ bearish | Bar spikes into resistance and closes back below it with an upper wick. |
| Support Rejection | ▲ bullish | Bar spikes into support and closes back above it with a lower wick. |

**Trend structure**

| Pattern | Direction | What it means |
|---|---|---|
| Break of Structure (up) | ▲ bullish | Close above the last swing high while the trend was up or ranging: trend continuation. |
| Break of Structure (down) | ▼ bearish | Close below the last swing low while the trend was down or ranging: trend continuation. |
| Change of Character (up) | ▲ bullish | Close above the last lower-high in a downtrend: first sign the trend may be turning. |
| Change of Character (down) | ▼ bearish | Close below the last higher-low in an uptrend: first sign the trend may be turning. |

## CLI

```bash
patterniq detect --tf 15m --bars 1500 --min-quality 0.7        # recent detections as a table (or --out file.csv)
patterniq detect --tf 5m --since 2025-03-01 --category chart   # only chart patterns since a date
patterniq stats  --tf 15m --bars 30000 --horizons 5 10 20      # scorecard in the terminal
patterniq check                                                # what is found in your database, and why not
patterniq inspect-db                                           # list tables and propose a config for your database
patterniq serve --host 0.0.0.0 --port 8000                     # API + dashboard
```

## HTTP API

| Endpoint | Purpose |
|---|---|
| `GET /api/meta` | symbol, available timeframes with date ranges, pattern catalogue |
| `GET /api/analysis?timeframe=15m&bars=600&end=2025-03-04&min_quality=0.5&categories=chart,level` | candles + detections + levels + swings + trend for a window (`start=` instead of `end=` pages forward) |
| `GET /api/stats?timeframe=15m&bars=20000&horizons=5,10,20&min_n=30` | scorecard rows |

Interactive docs at `/docs` while the server runs. There is no authentication, so keep it on `127.0.0.1` unless you put it behind something that has.

## Use it as a library

```python
from patterniq import analyze
from patterniq.data import make_source
from patterniq.config import Settings

df = make_source(Settings.load()).load("15m", limit=2000)   # DataFrame: open/high/low/close/volume, local-time index
result = analyze(df)
for d in result.detections:
    if d.category == "chart" and d.status == "confirmed":
        print(df.index[d.confirmed], d.pattern, d.direction, d.quality, d.meta.get("target"))
```

## Development

```bash
pytest                                   # 90+ tests; one extra test runs against real Postgres if you set:
PATTERNIQ_TEST_PG_URL=postgresql://user:pw@localhost/some_test_db pytest tests/test_data.py
cd web && npm run dev                    # hot-reload UI on :5173, proxies /api to :8000
```

Layout: `patterniq/core` (ATR, zigzag) · `patterniq/patterns` (candlestick, chart, levels, structure, catalog) · `patterniq/data` (sql, csv, demo, introspection) · `patterniq/stats.py` (scorecard) · `patterniq/api` · `web/`.

The tests include a **causality test** (analysing data cut at bar *t* gives exactly the same confirmed detections up to *t* as analysing everything) and a **null-data test** (on random returns no pattern may show an edge), which together guard against accidental lookahead.

## Known limits

* Rules are explicit heuristics tuned on synthetic formations, not learned from your trades; thresholds live in `AnalysisConfig` (`patterniq/analysis.py` and the `*Params` dataclasses). Expect to tune them on real data, and check the first weeks of signals against your own eye.
* Volume is not used (and the Nifty index has none). Overnight gaps are not treated specially on intraday charts. Single instrument per server. Live data is polled once a minute after each bar closes, not streamed tick by tick; alerts are for the timeframe on screen only.
* Patterns that span a session boundary on 1m/5m data are allowed; they can look different from the same pattern within one day.
