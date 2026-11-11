import numpy as np
import pandas as pd
import pytest

from patterniq.data.synthetic import candles_to_df, generate_demo_1m, resample_ohlc


def lead(direction: str, n: int = 24, end: float = 100.0, step: float = 1.0):
    """``n`` steady candles that finish at price ``end`` after moving in ``direction``."""
    rows = []
    p = end + (n * step if direction == "down" else -n * step)
    for _ in range(n):
        if direction == "down":
            o, c = p, p - step
            rows.append((o, o + 0.1, c - 0.1, c))
        else:
            o, c = p, p + step
            rows.append((o, c + 0.1, o - 0.1, c))
        p = c
    return rows


def flat(n: int = 24, price: float = 100.0):
    return [(price, price + 0.6, price - 0.6, price + (0.1 if i % 2 else -0.1)) for i in range(n)]


@pytest.fixture(scope="session")
def demo_15m() -> pd.DataFrame:
    return resample_ohlc(generate_demo_1m(days=120, seed=11), "15m")


@pytest.fixture(scope="session")
def iid_series():
    def make(n: int, seed: int) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        close = 20000 * np.exp(np.cumsum(rng.normal(0, 0.0015, n)))
        open_ = np.concatenate([[20000.0], close[:-1]])
        w = np.abs(rng.normal(0, 0.0008, (2, n))) * close
        idx = pd.date_range("2024-01-01 09:15", periods=n, freq="15min", name="time")
        return pd.DataFrame(
            {
                "open": open_,
                "high": np.maximum(open_, close) + w[0],
                "low": np.minimum(open_, close) - w[1],
                "close": close,
                "volume": 1.0,
            },
            index=idx,
        )

    return make


__all__ = ["candles_to_df", "lead", "flat"]
