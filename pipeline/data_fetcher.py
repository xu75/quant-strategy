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

# Canonical market data directory (committed to repo, CI-owned)
CANONICAL_MARKET_DIR = Path(__file__).parent.parent / "data" / "market"

# Legacy local path for development machines
LOCAL_HISTORY_PATH = Path.home() / "VSCode/SynologyDrive/backtest/history_data/normalized/BTC-USD_1h.csv"

# yfinance symbol mapping: local CSV name -> Yahoo Finance ticker
_YFINANCE_SYMBOL_MAP = {
    "MSTR_1h.csv": "MSTR",
    "BTC-USD_1h.csv": "BTC-USD",
    "QQQ_1h.csv": "QQQ",
    "MSTR_1d.csv": "MSTR",
    "BTC-USD_1d.csv": "BTC-USD",
    "QQQ_1d.csv": "QQQ",
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


_INTERVAL_COVERING = {
    "1h": ["5m", "15m", "30m"],
}


def _resample_to_target(df: pd.DataFrame, source_freq: str, target_freq: str) -> pd.DataFrame:
    """Resample finer-grained data to target frequency."""
    tmp = df.set_index("timestamp").sort_index()
    resampled = tmp.resample(target_freq, offset="0h").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    return resampled.reset_index()


def _resolve_csv_path(filename: str) -> Path | None:
    """Find a CSV file: canonical data/market/ first, then legacy local path."""
    canonical = CANONICAL_MARKET_DIR / filename
    if canonical.exists():
        return canonical
    legacy = LOCAL_HISTORY_PATH.parent / filename
    if legacy.exists():
        return legacy
    return None


def load_local_history_by_name(
    filename: str,
    target_bar: str = "1H",
) -> pd.DataFrame:
    """Load a named local history CSV from canonical or legacy directory.

    Resolution order:
      1. data/market/{filename}  (canonical, committed to repo, CI-readable)
      2. ~/VSCode/SynologyDrive/backtest/history_data/normalized/{filename}  (legacy dev)

    When the target file exists but a finer-grained file provides longer
    history (e.g. QQQ_5m.csv covers 2020+ while QQQ_1h.csv starts 2024),
    the finer file is resampled and prepended to fill the gap.

    Interval covering is source-constrained: if base file comes from canonical,
    finer files must also come from canonical (no cross-source mixing).

    No silent yfinance fallback — raises FileNotFoundError if no local file found.
    """
    path = _resolve_csv_path(filename)
    base_df = None
    base_source = None  # "canonical" or "legacy"

    if path is not None:
        base_df = load_local_history(path=path, target_bar=target_bar)
        base_source = "canonical" if path.parent == CANONICAL_MARKET_DIR else "legacy"

    # Interval-covering: try finer-grained files to extend history
    # Only search within the same source (canonical or legacy)
    target_lower = target_bar.lower()
    covering_intervals = _INTERVAL_COVERING.get(target_lower, [])
    stem = filename.rsplit("_", 1)[0]  # e.g. "QQQ" from "QQQ_1h.csv"

    for finer in covering_intervals:
        finer_file = f"{stem}_{finer}.csv"
        finer_path = _resolve_csv_path(finer_file)
        if finer_path is None:
            continue

        # Source constraint: only use finer file if it's from the same source as base
        finer_source = "canonical" if finer_path.parent == CANONICAL_MARKET_DIR else "legacy"
        if base_source is not None and finer_source != base_source:
            continue

        finer_raw = load_local_history(path=finer_path, target_bar=target_bar)
        if target_lower != finer:
            finer_raw = _resample_to_target(finer_raw, finer, target_lower)

        if base_df is not None:
            base_start = base_df["timestamp"].min()
            earlier = finer_raw[finer_raw["timestamp"] < base_start]
            if not earlier.empty:
                print(f"[data_fetcher] Covering {filename} with {finer_file}: "
                      f"prepending {len(earlier)} bars before {base_start}")
                base_df = pd.concat([earlier, base_df], ignore_index=True)
                base_df = base_df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
            break
        else:
            print(f"[data_fetcher] {filename} not found, resampling from {finer_file}")
            base_df = finer_raw
            base_source = finer_source
            break

    if base_df is not None:
        return base_df

    raise FileNotFoundError(
        f"Market data file '{filename}' not found in canonical ({CANONICAL_MARKET_DIR}) "
        f"or legacy ({LOCAL_HISTORY_PATH.parent}) directories"
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


def update_canonical_csv(filename: str, new_df: pd.DataFrame) -> int:
    """Append new rows to a canonical CSV in data/market/.

    Idempotent: deduplicates on datetime, only appends rows after the
    current last timestamp. Returns the number of new rows appended.

    Fail-closed: raises ValueError if gap exceeds threshold.
    Filters out unconfirmed bars (timestamp >= now - bar_interval).
    """
    path = CANONICAL_MARKET_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Canonical file {path} does not exist — seed it first")

    existing = pd.read_csv(path, parse_dates=["datetime"])
    existing["datetime"] = pd.to_datetime(existing["datetime"], utc=True)
    last_ts = existing["datetime"].max()

    # Normalize new data to canonical format
    if "timestamp" in new_df.columns and "datetime" not in new_df.columns:
        new_df = new_df.rename(columns={"timestamp": "datetime"})
    new_df["datetime"] = pd.to_datetime(new_df["datetime"], utc=True)

    # Filter out unconfirmed bars (current bar not yet closed)
    is_hourly = "_1h" in filename
    is_daily = "_1d" in filename
    bar_interval_hours = 1 if is_hourly else 24 if is_daily else 1
    now = pd.Timestamp.now(tz="UTC")
    cutoff = now - pd.Timedelta(hours=bar_interval_hours)
    new_df = new_df[new_df["datetime"] <= cutoff].copy()

    # Only keep rows strictly after the current last timestamp
    append_rows = new_df[new_df["datetime"] > last_ts].copy()
    if append_rows.empty:
        return 0

    append_rows = append_rows.sort_values("datetime")
    append_rows = append_rows[["datetime", "open", "high", "low", "close", "volume"]]

    # Gap check: first new row should be within expected interval of last existing row
    first_new = append_rows["datetime"].iloc[0]
    gap_hours = (first_new - last_ts).total_seconds() / 3600
    max_gap = 168 if is_hourly else 720 if is_daily else 168  # 1 week / 1 month
    if gap_hours > max_gap:
        raise ValueError(
            f"Gap of {gap_hours:.0f}h in {filename} exceeds threshold {max_gap}h "
            f"(last={last_ts}, first_new={first_new})"
        )

    # Append (no header)
    append_rows.to_csv(path, mode="a", header=False, index=False,
                       date_format="%Y-%m-%dT%H:%M:%SZ")
    return len(append_rows)


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
