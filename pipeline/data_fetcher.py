from __future__ import annotations

"""Market data fetcher.

Loads local historical data and fetches recent candles from OKX public API.
Falls back to Yahoo Finance for US equity data when local CSV is unavailable.
"""

import time
from pathlib import Path

import pandas as pd
import requests


OKX_BASE_URL = "https://www.okx.com"
MAX_CANDLES_PER_REQUEST = 100  # OKX limit per request

# Default path to local historical 1h data
LOCAL_HISTORY_PATH = Path.home() / "VSCode/SynologyDrive/backtest/history_data/normalized/BTC-USD_1h.csv"

# yfinance symbol mapping: local CSV name -> Yahoo Finance ticker
_YFINANCE_SYMBOL_MAP = {
    "MSTR_1h.csv": "MSTR",
    "BTC-USD_1h.csv": "BTC-USD",
}


def _fetch_yfinance(ticker: str, period: str = "730d", interval: str = "1h") -> pd.DataFrame:
    """Fetch historical data from Yahoo Finance via yfinance.

    Returns DataFrame with ['timestamp', 'open', 'high', 'low', 'close', 'volume'].
    Yahoo Finance 1H data is limited to ~730 days of history.
    """
    try:
        import yfinance as yf
    except ImportError:
        raise ImportError(
            "yfinance is required when local CSV is unavailable. "
            "Install with: pip install yfinance"
        )

    tk = yf.Ticker(ticker)
    hist = tk.history(period=period, interval=interval)
    if hist.empty:
        raise ValueError(f"No data returned from Yahoo Finance for {ticker}")

    hist = hist.reset_index()
    ts_col = "Datetime" if "Datetime" in hist.columns else "Date"
    hist = hist.rename(columns={
        ts_col: "timestamp",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    })
    hist["timestamp"] = pd.to_datetime(hist["timestamp"], utc=True)
    hist = hist[["timestamp", "open", "high", "low", "close", "volume"]]
    return hist.sort_values("timestamp").reset_index(drop=True)


def load_local_history_by_name(
    filename: str,
    target_bar: str = "1H",
) -> pd.DataFrame:
    """Load a named local history CSV from the normalized data directory.

    Falls back to Yahoo Finance when the local file is unavailable (e.g. in CI).

    Args:
        filename: CSV filename (e.g., "MSTR_1h.csv", "BTC-USD_1h.csv").
        target_bar: Target candle interval for resampling.

    Returns:
        DataFrame with ['timestamp', 'open', 'high', 'low', 'close', 'volume'].
    """
    path = LOCAL_HISTORY_PATH.parent / filename
    if path.exists():
        return load_local_history(path=path, target_bar=target_bar)

    ticker = _YFINANCE_SYMBOL_MAP.get(filename)
    if ticker:
        print(f"[data_fetcher] Local file {filename} not found, fetching from Yahoo Finance ({ticker})...")
        df = _fetch_yfinance(ticker)
        if target_bar.upper() != "1H":
            df = _resample_yfinance(df, target_bar)
        return df

    raise FileNotFoundError(
        f"Local file {path} not found and no Yahoo Finance mapping for {filename}"
    )


def _resample_yfinance(df: pd.DataFrame, target_bar: str) -> pd.DataFrame:
    """Resample yfinance 1H data to a coarser timeframe."""
    resample_map = {"4H": "4h", "1D": "1D"}
    freq = resample_map.get(target_bar.upper(), target_bar.lower())
    tmp = df.set_index("timestamp").sort_index()
    resampled = tmp.resample(freq, offset="0h").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    return resampled.reset_index()


def load_local_history(
    path: str | Path = LOCAL_HISTORY_PATH,
    target_bar: str = "4H",
) -> pd.DataFrame:
    """Load local historical CSV and optionally resample to target timeframe.

    For 1H target_bar, data is returned as-is (preserving original timestamps
    like 09:30 ET market open bars). Resampling only applies when aggregating
    to coarser timeframes (4H, 1D).

    Args:
        path: Path to normalized 1h CSV (columns: datetime,open,high,low,close,volume).
        target_bar: Target candle interval (e.g., "4H", "1H").

    Returns:
        DataFrame with columns ['timestamp', 'open', 'high', 'low', 'close', 'volume'],
        sorted oldest-first.
    """
    df = pd.read_csv(path, parse_dates=["datetime"])
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.set_index("datetime").sort_index()

    resample_map = {"4H": "4h", "1H": "1h", "1D": "1D"}
    freq = resample_map.get(target_bar, target_bar.lower())

    if freq == "1h":
        # 1H source data: preserve original timestamps (including half-hour bars)
        resampled = df.reset_index().rename(columns={"datetime": "timestamp"})
        return resampled[["timestamp", "open", "high", "low", "close", "volume"]]

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
