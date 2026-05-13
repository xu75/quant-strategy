from __future__ import annotations

"""CI-owned market data updater.

Fetches new candles from external sources (Yahoo Finance for equities,
OKX for crypto) and appends them to canonical CSVs in data/market/.

Idempotent: safe to run repeatedly. Only appends rows after the last
existing timestamp. Deduplicates on datetime.

Usage:
    python -m pipeline.update_market_data
"""

import sys
from pathlib import Path

import pandas as pd

from pipeline.data_fetcher import (
    CANONICAL_MARKET_DIR,
    update_canonical_csv,
    _fetch_yfinance,
    fetch_candles,
)

LOG_PREFIX = "[update-data]"

# Sources to update: (filename, fetch_function_args)
EQUITY_SOURCES = [
    ("MSTR_1h.csv", "MSTR", "1h", "60d"),
    ("QQQ_1h.csv", "QQQ", "1h", "60d"),
    ("MSTR_1d.csv", "MSTR", "1d", "1y"),
    ("QQQ_1d.csv", "QQQ", "1d", "1y"),
    ("SPY_1d.csv", "SPY", "1d", "1y"),
]

CRYPTO_SOURCES = [
    ("BTC-USD_1h.csv", "BTC-USDT", "1H", 300),
    ("BTC-USD_1d.csv", "BTC-USDT", "1D", 100),
]


def _update_equity(filename: str, ticker: str, interval: str, period: str) -> int:
    """Fetch equity data from Yahoo Finance and append to canonical CSV."""
    print(f"{LOG_PREFIX} Fetching {ticker} ({interval}) from Yahoo Finance...")
    df = _fetch_yfinance(ticker, period=period, interval=interval)
    print(f"{LOG_PREFIX}   Got {len(df)} candles")
    n = update_canonical_csv(filename, df)
    print(f"{LOG_PREFIX}   Appended {n} new rows to {filename}")
    return n


def _update_crypto(filename: str, symbol: str, bar: str, limit: int) -> int:
    """Fetch crypto data from OKX and append to canonical CSV."""
    print(f"{LOG_PREFIX} Fetching {symbol} ({bar}) from OKX...")
    df = fetch_candles(symbol=symbol, bar=bar, limit=limit)
    print(f"{LOG_PREFIX}   Got {len(df)} candles")
    n = update_canonical_csv(filename, df)
    print(f"{LOG_PREFIX}   Appended {n} new rows to {filename}")
    return n


def run_update() -> bool:
    """Update all canonical market data files. Returns True if any updated."""
    if not CANONICAL_MARKET_DIR.exists():
        print(f"{LOG_PREFIX} ERROR: {CANONICAL_MARKET_DIR} does not exist")
        sys.exit(1)

    total_new = 0
    failed = []

    for filename, ticker, interval, period in EQUITY_SOURCES:
        csv_path = CANONICAL_MARKET_DIR / filename
        if not csv_path.exists():
            print(f"{LOG_PREFIX} SKIP: {filename} not seeded yet")
            continue
        try:
            total_new += _update_equity(filename, ticker, interval, period)
        except Exception as e:
            print(f"{LOG_PREFIX} FAILED: {filename} — {e}")
            failed.append(filename)

    for filename, symbol, bar, limit in CRYPTO_SOURCES:
        csv_path = CANONICAL_MARKET_DIR / filename
        if not csv_path.exists():
            print(f"{LOG_PREFIX} SKIP: {filename} not seeded yet")
            continue
        try:
            total_new += _update_crypto(filename, symbol, bar, limit)
        except Exception as e:
            print(f"{LOG_PREFIX} FAILED: {filename} — {e}")
            failed.append(filename)

    print(f"{LOG_PREFIX} Total new rows appended: {total_new}")

    if failed:
        print(f"{LOG_PREFIX} ERROR: {len(failed)} source(s) failed: {failed}")
        sys.exit(1)

    return total_new > 0


if __name__ == "__main__":
    run_update()
