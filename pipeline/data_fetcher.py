"""Market data fetcher.

Loads local historical data and fetches recent candles from OKX public API.
"""

import time
from pathlib import Path

import pandas as pd
import requests


OKX_BASE_URL = "https://www.okx.com"
MAX_CANDLES_PER_REQUEST = 100  # OKX limit per request

# Default path to local historical 1h data
LOCAL_HISTORY_PATH = Path.home() / "VSCode/SynologyDrive/backtest/history_data/normalized/BTC-USD_1h.csv"


def load_local_history(
    path: str | Path = LOCAL_HISTORY_PATH,
    target_bar: str = "4H",
) -> pd.DataFrame:
    """Load local historical CSV and resample to target timeframe.

    Args:
        path: Path to normalized 1h CSV (columns: datetime,open,high,low,close,volume).
        target_bar: Target candle interval (e.g., "4H").

    Returns:
        DataFrame with columns ['timestamp', 'open', 'high', 'low', 'close', 'volume'],
        sorted oldest-first.
    """
    df = pd.read_csv(path, parse_dates=["datetime"])
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.set_index("datetime").sort_index()

    resample_map = {"4H": "4h", "1H": "1h", "1D": "1D"}
    freq = resample_map.get(target_bar, target_bar.lower())

    resampled = df.resample(freq, offset="0h").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()

    resampled = resampled.reset_index().rename(columns={"datetime": "timestamp"})
    return resampled


def fetch_candles(
    symbol: str = "BTC-USDT",
    bar: str = "4H",
    limit: int = 300,
    proxy: dict | None = None,
    timeout: int = 15,
) -> pd.DataFrame:
    """Fetch recent candles from OKX public API.

    Only returns confirmed (closed) candles.

    Args:
        symbol: Trading pair (e.g., "BTC-USDT").
        bar: Candle interval (e.g., "4H", "1D").
        limit: Number of candles to fetch.
        proxy: Optional proxy dict for requests.
        timeout: Request timeout in seconds.

    Returns:
        DataFrame with columns ['timestamp', 'open', 'high', 'low', 'close', 'volume'],
        sorted oldest-first.
    """
    all_records = []
    after = ""

    while len(all_records) < limit:
        batch_limit = min(MAX_CANDLES_PER_REQUEST, limit - len(all_records))
        url = f"{OKX_BASE_URL}/api/v5/market/candles"
        params = {"instId": symbol, "bar": bar, "limit": str(batch_limit)}
        if after:
            params["after"] = after

        resp = requests.get(url, params=params, proxies=proxy, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != "0" or not data.get("data"):
            break

        records = data["data"]
        # Only keep confirmed (closed) candles
        confirmed = [r for r in records if str(r[-1]) == "1"]
        if not confirmed:
            break

        all_records.extend(confirmed)

        # OKX returns newest first; use oldest timestamp as 'after' cursor
        after = confirmed[-1][0]

        if len(records) < batch_limit:
            break

        time.sleep(0.1)  # rate limit courtesy

    if not all_records:
        raise ValueError(f"No confirmed candle data returned for {symbol}")

    df = pd.DataFrame(all_records)
    df.columns = [
        "timestamp_ms", "open", "high", "low", "close",
        "volume", "vol_currency", "vol_currency_quote", "confirm",
    ]

    df["timestamp"] = pd.to_datetime(df["timestamp_ms"].astype(int), unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    df = df.sort_values("timestamp").reset_index(drop=True)

    return df


def fetch_historical_candles(
    symbol: str = "BTC-USDT",
    bar: str = "4H",
    limit: int = 1440,
    proxy: dict | None = None,
    timeout: int = 15,
) -> pd.DataFrame:
    """Fetch extended historical candles by paginating through OKX API.

    OKX /market/history-candles supports older data beyond the recent window.

    Args:
        symbol: Trading pair.
        bar: Candle interval.
        limit: Total candles desired (will paginate).
        proxy: Optional proxy.
        timeout: Request timeout.

    Returns:
        DataFrame sorted oldest-first.
    """
    all_records = []
    after = ""

    while len(all_records) < limit:
        batch_limit = min(MAX_CANDLES_PER_REQUEST, limit - len(all_records))
        url = f"{OKX_BASE_URL}/api/v5/market/history-candles"
        params = {"instId": symbol, "bar": bar, "limit": str(batch_limit)}
        if after:
            params["after"] = after

        resp = requests.get(url, params=params, proxies=proxy, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != "0" or not data.get("data"):
            break

        records = data["data"]
        confirmed = [r for r in records if str(r[-1]) == "1"]
        if not confirmed:
            break

        all_records.extend(confirmed)
        after = confirmed[-1][0]

        if len(records) < batch_limit:
            break

        time.sleep(0.2)

    # Combine with recent candles
    recent = fetch_candles(symbol=symbol, bar=bar, limit=300, proxy=proxy, timeout=timeout)

    if all_records:
        df_hist = pd.DataFrame(all_records)
        df_hist.columns = [
            "timestamp_ms", "open", "high", "low", "close",
            "volume", "vol_currency", "vol_currency_quote", "confirm",
        ]
        df_hist["timestamp"] = pd.to_datetime(df_hist["timestamp_ms"].astype(int), unit="ms", utc=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df_hist[col] = df_hist[col].astype(float)
        df_hist = df_hist[["timestamp", "open", "high", "low", "close", "volume"]]

        combined = pd.concat([df_hist, recent], ignore_index=True)
        combined = combined.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
        return combined

    return recent
