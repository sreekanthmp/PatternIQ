"""Angel One SmartAPI market-data feed (login + historical candles only).

Safety notes, learned from reading the SDK (smartapi-python 1.5.x):

* On any failed call the SDK logs the *full request headers and body* at error level, which for a
  login includes the API key, client PIN and the TOTP code, both to the screen and to a file
  (``logs/<date>/app.log`` in the current folder). This module prevents both (see
  ``_default_sdk_factory``) and builds its own messages, which are passed through ``redact``.
* ``import SmartApi`` also asks ``api.ipify.org`` for your public IP (Angel requires it as a header).
* Nothing here can place orders: only ``generateSession``, ``getCandleData`` and ``terminateSession``
  are ever called.
"""

from __future__ import annotations

import time
from typing import Any, Callable

import pandas as pd

from .env import AngelCredentials, LiveConfigError, redact
from .totp import seconds_left, totp

INTERVALS = {"1m": "ONE_MINUTE", "5m": "FIVE_MINUTE", "15m": "FIFTEEN_MINUTE", "1d": "ONE_DAY"}
# Documented per-request limits are 30 / 100 / 200 / 2000 days; stay safely inside them.
MAX_SPAN_DAYS = {"1m": 25, "5m": 80, "15m": 160, "1d": 1500}
# NSE index tokens in Angel's instrument master. `patterniq live-check` prints the prices it gets back
# so a wrong token is obvious before anything is written.
INDEX_TOKENS = {"NIFTY": ("NSE", "99926000"), "NIFTY50": ("NSE", "99926000"), "BANKNIFTY": ("NSE", "99926009")}
AUTH_CODES = {"AG8001", "AG8002", "AG8003", "AB8050", "AB8051"}


class FeedError(RuntimeError):
    """A feed call failed (message is already redacted)."""


class FeedAuthError(FeedError):
    """Login failed or the session could not be renewed."""


class FeedRateLimited(FeedError):
    """Angel is throttling us; back off."""


def resolve_token(symbol: str, exchange: str | None = None, token: str | None = None) -> tuple[str, str]:
    if token:
        return exchange or "NSE", token
    key = "".join(ch for ch in symbol.upper() if ch.isalnum())
    if key in INDEX_TOKENS:
        return INDEX_TOKENS[key]
    raise LiveConfigError(
        f"No built-in Angel token for symbol {symbol!r}. Pass --token (and --exchange); look it up in Angel's "
        "OpenAPIScripMaster.json."
    )


def parse_candles(rows: list | None) -> pd.DataFrame:
    """[[ '2026-11-11T09:15:00+05:30', o, h, l, c, v ], ...] -> frame with a naive IST index."""
    cols = ["open", "high", "low", "close", "volume"]
    if not rows:
        return pd.DataFrame(columns=cols, index=pd.DatetimeIndex([], name="time"), dtype=float)
    df = pd.DataFrame(rows, columns=["time", *cols])
    ts = pd.DatetimeIndex(pd.to_datetime(df.pop("time")))
    ts = ts.tz_convert("Asia/Kolkata").tz_localize(None) if ts.tz is not None else ts
    df.index = ts.rename("time")
    df = df.astype(float)
    return df[~df.index.duplicated(keep="last")].sort_index()


def _silence_sdk_logging() -> None:
    import logging

    import logzero

    logzero.logger.disabled = True
    logzero.logger.propagate = False
    for h in list(logzero.logger.handlers):
        try:
            h.close()
        finally:
            logzero.logger.removeHandler(h)
    logzero.logger.addHandler(logging.NullHandler())


def _default_sdk_factory(api_key: str):
    """Build the SDK client without letting it log anywhere.

    ``SmartConnect.__init__`` calls ``logzero.logfile(...)``, which writes ``logs/<date>/app.log`` in the
    current folder and records the full request (PIN, API key, one-time code) whenever a call fails. So:
    that call is neutralised, construction happens in a throw-away directory (the SDK also creates the
    folder itself), and the logger is silenced again afterwards.
    """
    import os
    import shutil
    import tempfile

    try:
        import logzero

        _silence_sdk_logging()  # before SmartApi is imported
        from SmartApi import SmartConnect
    except ModuleNotFoundError as e:  # pragma: no cover - depends on the environment
        raise LiveConfigError(
            f"The Angel One SDK is not installed ({e.name} missing). Install the live extras: pip install -e \".[live]\""
        ) from None
    original, cwd, scratch = logzero.logfile, os.getcwd(), tempfile.mkdtemp(prefix="patterniq-sdk-")
    logzero.logfile = lambda *a, **k: None
    try:
        os.chdir(scratch)
        client = SmartConnect(api_key=api_key)
    finally:
        os.chdir(cwd)
        logzero.logfile = original
        _silence_sdk_logging()
        shutil.rmtree(scratch, ignore_errors=True)
    return client


class AngelFeed:
    def __init__(
        self,
        creds: AngelCredentials,
        *,
        sdk_factory: Callable[[str], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
        min_interval: float = 1.2,
    ):
        self.creds = creds
        self._factory = sdk_factory or _default_sdk_factory
        self._sleep, self._now = sleep, now
        self.min_interval = min_interval
        self._sdk: Any = None
        self._runtime_secrets: list[str] = []
        self._last_call = 0.0

    # ---- helpers ---------------------------------------------------------------------------
    def _clean(self, text: object) -> str:
        return redact(text, self.creds.secrets() + self._runtime_secrets)

    def _throttle(self) -> None:
        wait = self.min_interval - (self._now() - self._last_call)
        if wait > 0:
            self._sleep(wait)
        self._last_call = self._now()

    # ---- session ---------------------------------------------------------------------------
    def login(self) -> None:
        """Log in. At most two attempts (a TOTP code can expire mid-request); never loops on failure."""
        self._sdk = self._factory(self.creds.api_key)
        for attempt in (1, 2):
            if seconds_left(self._now()) < 3:  # don't submit a code that is about to expire
                self._sleep(seconds_left(self._now()) + 0.5)
            code = totp(self.creds.totp_secret, at=self._now())
            self._runtime_secrets.append(code)  # short-lived, but still a credential: keep it out of messages
            try:
                resp = self._sdk.generateSession(self.creds.client_id, self.creds.password, code)
            except Exception as e:  # network / SDK errors
                raise FeedAuthError(f"login request failed: {type(e).__name__}: {self._clean(e)}") from None
            if isinstance(resp, dict) and resp.get("status"):
                data = resp.get("data") or {}
                self._runtime_secrets = [str(data.get(k)) for k in ("jwtToken", "refreshToken", "feedToken") if data.get(k)]
                return
            msg = self._clean((resp or {}).get("message", "unknown error")) if isinstance(resp, dict) else "unexpected response"
            if attempt == 1 and "totp" in msg.lower():
                self._sleep(seconds_left(self._now()) + 1.0)  # wait for the next 30 s code and try once more
                continue
            raise FeedAuthError(f"login failed: {msg} ({(resp or {}).get('errorcode', '')})")

    def close(self) -> None:
        if self._sdk is not None:
            try:
                self._sdk.terminateSession(self.creds.client_id)
            except Exception:
                pass
            self._sdk = None

    # ---- calls -----------------------------------------------------------------------------
    def _call(self, fn: Callable[[], Any]) -> Any:
        for attempt in (1, 2):
            if self._sdk is None:
                self.login()
            self._throttle()
            try:
                resp = fn()
            except Exception as e:
                text = self._clean(e)
                low = text.lower()
                if type(e).__name__ == "TokenException":
                    resp = {"status": False, "message": text, "errorcode": "AG8002"}
                elif "access rate" in low or "exceeding" in low:
                    raise FeedRateLimited(text) from None
                else:
                    raise FeedError(f"{type(e).__name__}: {text}") from None
            if isinstance(resp, dict) and resp.get("status") is False:
                code, msg = str(resp.get("errorcode", "")), self._clean(resp.get("message", ""))
                if code in AUTH_CODES or "invalid token" in msg.lower() or "token" in msg.lower() and "expired" in msg.lower():
                    if attempt == 1:
                        self._sdk = None  # session expired: log in again once and retry
                        continue
                    raise FeedAuthError(f"session could not be renewed: {msg} ({code})")
                if "access rate" in msg.lower() or "exceeding" in msg.lower():
                    raise FeedRateLimited(msg)
                raise FeedError(f"{msg} ({code})")
            return resp
        raise FeedAuthError("session could not be renewed")  # pragma: no cover

    def candles(self, exchange: str, token: str, tf: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        params = {
            "exchange": exchange, "symboltoken": token, "interval": INTERVALS[tf],
            "fromdate": start.strftime("%Y-%m-%d %H:%M"), "todate": end.strftime("%Y-%m-%d %H:%M"),
        }
        resp = self._call(lambda: self._sdk.getCandleData(dict(params)))
        return parse_candles((resp or {}).get("data"))

    def fetch_range(self, exchange: str, token: str, tf: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        """``candles`` split into chunks the API accepts."""
        if end < start:
            return parse_candles(None)
        span = pd.Timedelta(days=MAX_SPAN_DAYS[tf])
        frames, cur = [], start
        while cur <= end:
            chunk_end = min(end, cur + span)
            frames.append(self.candles(exchange, token, tf, cur, chunk_end))
            cur = chunk_end + pd.Timedelta(minutes=1)
        out = pd.concat(frames)
        return out[~out.index.duplicated(keep="last")].sort_index()
