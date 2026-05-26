from __future__ import annotations

"""A-share ETF data updater for N100 Guard-Z strategy.

Fetches daily OHLCV and NAV data for Nasdaq-100 tracking ETFs listed on
A-share markets using AKShare (open-source, no token required).

Canonical output files in data/market/:
  - {code}_1d.csv: daily OHLCV + adj_close for each ETF
  - n100_etf_nav.csv: daily NAV for all ETFs (wide format)

Usage:
    python -m pipeline.update_ashare_data
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd

CANONICAL_MARKET_DIR = Path(__file__).parent.parent / "data" / "market"
LOG_PREFIX = "[update-ashare]"

# N100 ETF pool: code -> name
N100_ETF_POOL = {
    "513100": "国泰纳指100",
    "159941": "易方达纳指100",
    "513300": "华夏纳指100",
    "159501": "富国纳指100",
    "159513": "鹏华纳指100",
    "159659": "景顺纳指100",
    "159632": "中欧纳指100",
    "159660": "博时纳指100",
    "159696": "华安纳指100",
    "513110": "华安纳指100ETF",
    "513390": "天弘纳指100",
}


def _fetch_etf_daily(code: str, start_date: str = "20130101") -> pd.DataFrame:
    """Fetch daily OHLCV for a single A-share ETF via AKShare."""
    try:
        import akshare as ak
    except ImportError:
        raise ImportError("akshare is required. Install with: pip install akshare")

    try:
        df = ak.fund_etf_hist_em(
            symbol=code,
            period="daily",
            start_date=start_date,
            adjust="",
        )
    except Exception as exc:
        print(f"{LOG_PREFIX} WARN: fund_etf_hist_em failed for {code}: {exc}")
        df = ak.fund_etf_hist_sina(symbol=_sina_symbol(code))

    if df.empty:
        return pd.DataFrame()

    df = _normalize_daily_df(df)
    start_ts = pd.to_datetime(start_date, format="%Y%m%d", utc=True)
    df = df[df["datetime"] >= start_ts]
    return df.sort_values("datetime").reset_index(drop=True)


def _sina_symbol(code: str) -> str:
    """Convert an A-share ETF code to Sina's exchange-prefixed symbol."""
    if code.startswith("5"):
        return f"sh{code}"
    return f"sz{code}"


def _normalize_daily_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize AKShare ETF daily endpoint variants to canonical columns."""
    df = df.rename(columns={
        "日期": "datetime",
        "date": "datetime",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
    })
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df[["datetime", "open", "high", "low", "close", "volume"]]
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["datetime", "open", "high", "low", "close"])


def _fetch_etf_nav(code: str, start_date: str = "20130101") -> pd.DataFrame:
    """Fetch daily NAV (unit + cumulative) for a single ETF via AKShare."""
    try:
        import akshare as ak
    except ImportError:
        raise ImportError("akshare is required. Install with: pip install akshare")

    df = ak.fund_etf_fund_info_em(fund=code, start_date=start_date)
    if df.empty:
        return pd.DataFrame()

    df = df.rename(columns={
        "净值日期": "datetime",
        "单位净值": "nav",
        "累计净值": "cum_nav",
    })
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    cols = ["datetime", "nav"]
    if "cum_nav" in df.columns:
        cols.append("cum_nav")
    df = df[cols]
    return df.sort_values("datetime").reset_index(drop=True)


def _compute_adj_close(df: pd.DataFrame, nav_df: pd.DataFrame = None) -> pd.DataFrame:
    """Compute adj_close using terminal split factor (constant multiplier).

    terminal_split_factor = cum_nav_terminal / nav_terminal
    adj_close = close * terminal_split_factor

    This uses the TERMINAL (latest) ratio as a constant across the entire
    series, ensuring no fake returns appear on split dates.
    If NAV data unavailable, falls back to adj_close = close (factor = 1.0).
    """
    df = df.copy()

    if nav_df is not None and "cum_nav" in nav_df.columns and "nav" in nav_df.columns:
        # Get terminal values (latest row with valid data)
        valid = nav_df.dropna(subset=["nav", "cum_nav"])
        if not valid.empty:
            terminal = valid.iloc[-1]
            nav_terminal = terminal["nav"]
            cum_nav_terminal = terminal["cum_nav"]
            if nav_terminal > 0:
                split_factor = cum_nav_terminal / nav_terminal
                df["adj_close"] = df["close"] * split_factor
                return df

    # Fallback: no split detected
    df["adj_close"] = df["close"]
    return df


def _update_single_etf(code: str) -> int:
    """Update daily data for one ETF. Returns number of new rows."""
    filename = f"{code}_1d.csv"
    csv_path = CANONICAL_MARKET_DIR / filename

    # Determine start date
    if csv_path.exists():
        existing = pd.read_csv(csv_path, parse_dates=["datetime"])
        last_date = existing["datetime"].max()
        start_date = (last_date - timedelta(days=5)).strftime("%Y%m%d")
    else:
        start_date = "20130101"
        existing = pd.DataFrame()

    df = _fetch_etf_daily(code, start_date=start_date)
    if df.empty:
        return 0

    # Fetch NAV for split factor calculation
    try:
        nav_df = _fetch_etf_nav(code, start_date="20130101")
    except Exception:
        nav_df = None

    df = _compute_adj_close(df, nav_df=nav_df)

    if not existing.empty:
        # Append only new rows
        df = df[df["datetime"] > last_date]
        if df.empty:
            return 0
        combined = pd.concat([existing, df], ignore_index=True)
    else:
        combined = df

    combined = combined.drop_duplicates(subset=["datetime"]).sort_values("datetime")
    combined.to_csv(csv_path, index=False)
    return len(df)


def _update_nav_data() -> int:
    """Update NAV data for all ETFs into a single wide-format CSV."""
    csv_path = CANONICAL_MARKET_DIR / "n100_etf_nav.csv"

    if csv_path.exists():
        existing = pd.read_csv(csv_path, parse_dates=["datetime"])
        last_date = existing["datetime"].max()
        start_date = (last_date - timedelta(days=5)).strftime("%Y%m%d")
    else:
        existing = pd.DataFrame()
        start_date = "20130101"
        last_date = None

    all_navs = []
    for code in N100_ETF_POOL:
        try:
            nav_df = _fetch_etf_nav(code, start_date=start_date)
            if not nav_df.empty:
                # Only keep datetime + nav (renamed to code) for wide table
                # cum_nav is used only in _compute_adj_close, not stored in NAV wide table
                nav_df = nav_df[["datetime", "nav"]].rename(columns={"nav": code})
                all_navs.append(nav_df)
        except Exception as e:
            print(f"{LOG_PREFIX} WARN: NAV fetch failed for {code}: {e}")

    if not all_navs:
        return 0

    # Merge all NAVs on datetime
    merged = all_navs[0]
    for nav_df in all_navs[1:]:
        merged = merged.merge(nav_df, on="datetime", how="outer")
    merged = merged.sort_values("datetime")

    if not existing.empty and last_date is not None:
        new_rows = merged[merged["datetime"] > last_date]
        if new_rows.empty:
            return 0
        combined = pd.concat([existing, new_rows], ignore_index=True)
    else:
        combined = merged

    combined = combined.drop_duplicates(subset=["datetime"]).sort_values("datetime")
    combined.to_csv(csv_path, index=False)
    return len(merged) if existing.empty else len(new_rows)


def run_update() -> bool:
    """Update all A-share ETF data. Returns True if any updated."""
    if not CANONICAL_MARKET_DIR.exists():
        print(f"{LOG_PREFIX} ERROR: {CANONICAL_MARKET_DIR} does not exist")
        sys.exit(1)

    total_new = 0
    failed = []

    # Update daily OHLCV for each ETF
    for code, name in N100_ETF_POOL.items():
        print(f"{LOG_PREFIX} Updating {code} ({name})...")
        try:
            n = _update_single_etf(code)
            total_new += n
            print(f"{LOG_PREFIX}   Appended {n} new rows")
        except Exception as e:
            print(f"{LOG_PREFIX} FAILED: {code} — {e}")
            failed.append(code)

    # Update NAV data
    print(f"{LOG_PREFIX} Updating NAV data...")
    try:
        n = _update_nav_data()
        total_new += n
        print(f"{LOG_PREFIX}   NAV: {n} new rows")
    except Exception as e:
        print(f"{LOG_PREFIX} FAILED: NAV update — {e}")
        failed.append("NAV")

    print(f"{LOG_PREFIX} Total new rows: {total_new}")

    if failed:
        print(f"{LOG_PREFIX} ERROR: {len(failed)} source(s) failed: {failed}")
        sys.exit(1)

    return total_new > 0


if __name__ == "__main__":
    run_update()
