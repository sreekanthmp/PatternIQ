"""Keep the candle table up to date from a broker feed: backfill the gap, then append each closed bar."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd
from sqlalchemy.exc import SQLAlchemyError

from .angel import FeedAuthError, FeedError, FeedRateLimited
from .bars import aggregate, closed_only
from .clock import MINUTE, bucket_start, ist_now, is_weekday, market_open, next_open, session_close

INTRADAY = (("1m", 1), ("5m", 5), ("15m", 15))


@dataclass
class SyncResult:
    inserted: dict[str, int] = field(default_factory=dict)
    window: tuple[pd.Timestamp, pd.Timestamp] | None = None
    fetched: int = 0

    @property
    def total(self) -> int:
        return sum(self.inserted.values())


class Ingestor:
    def __init__(
        self, feed, sink, *, exchange: str, token: str,
        timeframes: tuple[str, ...] = ("1m", "5m", "15m", "1d"),
        now_fn: Callable[[], pd.Timestamp] = ist_now, sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = print, dry_run: bool = False,
        max_backfill_days: int = 45, delay_s: float = 5.0, grace_min: float = 5.0,
    ):
        self.feed, self.sink = feed, sink
        self.exchange, self.token = exchange, token
        self.tfs = tuple(timeframes)
        self.now_fn, self.sleep, self.log = now_fn, sleep, log
        self.dry_run = dry_run
        self.max_backfill = pd.Timedelta(days=max_backfill_days)
        self.delay_s = delay_s
        self.grace = pd.Timedelta(minutes=grace_min)
        self._daily_checked: pd.Timestamp | None = None

    # ---- one pass --------------------------------------------------------------------------
    def window_start(self, now: pd.Timestamp) -> pd.Timestamp:
        """Earliest minute any intraday timeframe is missing, bounded by ``max_backfill_days``."""
        floor = now.normalize() - self.max_backfill
        starts = []
        for tf, mins in INTRADAY:
            if tf in self.tfs:
                last = self.sink.last_bar(tf)
                starts.append(last + pd.Timedelta(minutes=mins) if last is not None else floor)
        start = max(min(starts), floor)
        return bucket_start(start, 15) if start.hour * 60 + start.minute >= 9 * 60 + 15 else start

    def sync_once(self) -> SyncResult:
        now = self.now_fn()
        res = SyncResult()
        if any(tf in self.tfs for tf, _ in INTRADAY):
            start = self.window_start(now)
            res.window = (start, now)
            raw = self.feed.fetch_range(self.exchange, self.token, "1m", start, now)
            m1 = closed_only(raw, MINUTE, now, self.delay_s)
            res.fetched = len(m1)
            if "1m" in self.tfs:
                res.inserted["1m"] = self.sink.insert("1m", m1, self.dry_run)
            for tf, mins in INTRADAY[1:]:
                if tf in self.tfs:
                    res.inserted[tf] = self.sink.insert(tf, aggregate(m1, mins, now, self.delay_s, self.grace), self.dry_run)
        if "1d" in self.tfs:
            res.inserted["1d"] = self._sync_daily(now)
        return res

    def _sync_daily(self, now: pd.Timestamp) -> int:
        """Exchange daily bars for finished sessions (today's only after the close)."""
        today = now.normalize()
        last_day = today if now >= session_close(today) + pd.Timedelta(seconds=self.delay_s) else today - pd.Timedelta(days=1)
        stored = self.sink.last_bar("1d")
        first = (stored.normalize() + pd.Timedelta(days=1)) if stored is not None else today - self.max_backfill
        if self._daily_checked is not None:
            first = max(first, self._daily_checked + pd.Timedelta(days=1))
        first = max(first, today - self.max_backfill)
        if first > last_day:
            return 0
        df = self.feed.fetch_range(self.exchange, self.token, "1d", first, last_day + pd.Timedelta(hours=23, minutes=59))
        self._daily_checked = last_day                      # holidays return no bar: don't ask again every minute
        return self.sink.insert("1d", df, self.dry_run)

    # ---- the loop --------------------------------------------------------------------------
    def _stamp(self, msg: str) -> None:
        self.log(f"[{self.now_fn():%Y-%m-%d %H:%M:%S}] {msg}")

    def _report(self, res: SyncResult, first: bool = False) -> None:
        parts = [f"{tf}+{n}" for tf, n in res.inserted.items() if n]
        verb = "would insert" if self.dry_run else "inserted"
        if parts:
            self._stamp(f"{verb} " + " ".join(parts))
        elif first:
            self._stamp("up to date (nothing to insert)")

    def safe_sync(self, failures: int) -> int:
        """One pass that survives transient trouble. Returns the new consecutive-failure count."""
        try:
            res = self.sync_once()
            self._report(res)
            return 0
        except FeedRateLimited as e:
            self._stamp(f"Angel is rate limiting requests ({e}); pausing 60 s")
            self.sleep(60)
        except FeedAuthError as e:
            raise  # credentials / session problem: retrying would only risk locking the account
        except (FeedError, SQLAlchemyError) as e:
            first = (str(getattr(e, "orig", None) or e).strip().splitlines() or [type(e).__name__])[0]
            self._stamp(f"temporary problem: {type(e).__name__}: {first[:200]}")
            self.sleep(min(60, 5 * 2**min(failures, 4)))
        failures += 1
        if failures >= 10:
            raise FeedError("10 consecutive failures; stopping (see the messages above)")
        return failures

    def run_forever(self, max_loops: int | None = None) -> None:
        self._stamp(("DRY RUN: nothing will be written. " if self.dry_run else "") + "catching up...")
        res = self.sync_once()
        self._report(res, first=True)
        failures, loops, last_idle_note = 0, 0, None
        while max_loops is None or loops < max_loops:
            loops += 1
            now = self.now_fn()
            after_close = is_weekday(now) and session_close(now) <= now < session_close(now) + pd.Timedelta(minutes=10)
            if market_open(now) or after_close:
                wait = 60 - now.second + self.delay_s
                self.sleep(wait)
                failures = self.safe_sync(failures)
            else:
                nxt = next_open(now)
                if last_idle_note != nxt:
                    self._stamp(f"market closed; next session opens {nxt:%a %d %b %H:%M}. Waiting.")
                    last_idle_note = nxt
                self.sleep(min(60.0, max(1.0, (nxt - now).total_seconds() - 30)))
