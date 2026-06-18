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
import yaml

CANONICAL_MARKET_DIR = Path(__file__).parent.parent / "data" / "market"
SPLIT_FACTORS_PATH = CANONICAL_MARKET_DIR / "split_factors.yaml"
LOG_PREFIX = "[update-ashare]"

# Tolerance for adj_close regression guard: if existing adj_close for an
# overlap row would change by more than this fraction, reject the update.
ADJ_CLOSE_DRIFT_TOLERANCE = 0.10


def _load_etf_pool() -> dict[str, str]:
    """Load ETF pool from single source of truth (etf_pool.yaml)."""
    config_path = Path(__file__).parent.parent / "strategies" / "n100_guard_z" / "etf_pool.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        pool = yaml.safe_load(f)
    return {etf["code"]: etf["official_short_name"] for etf in pool["etfs"]}


def _load_split_factors() -> dict[str, list[dict]]:
    """Load pinned split factors from split_factors.yaml.

    Returns dict mapping ETF code to list of split events, each with:
      event_date (pd.Timestamp), factor (float), pre_factor (float).
    """
    if not SPLIT_FACTORS_PATH.exists():
        return {}
    with open(SPLIT_FACTORS_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    result: dict[str, list[dict]] = {}
    for entry in data.get("splits", []):
        code = entry["code"]
        event = {
            "event_date": pd.Timestamp(entry["event_date"], tz="UTC"),
            "factor": float(entry["factor"]),
            "pre_factor": float(entry.get("pre_factor", 1.0)),
        }
        result.setdefault(code, []).append(event)
    return result


N100_ETF_POOL = _load_etf_pool()
PINNED_SPLITS = _load_split_factors()


def _to_utc_ns(values) -> pd.Series:
    """Normalize datetimes to pandas nanosecond UTC for merge_asof."""
    return pd.to_datetime(values, utc=True).astype("datetime64[ns, UTC]")


def _align_split_factor_to_price_jumps(
    close: pd.Series,
    split_factor: pd.Series,
) -> pd.Series:
    """Delay large split-factor jumps until the market price actually jumps."""
    close = close.astype(float).reset_index(drop=True)
    factor = split_factor.astype(float).ffill().fillna(1.0).reset_index(drop=True)
    if factor.empty:
        return factor

    current = float(factor.iloc[0])
    pending = None
    aligned = [current]

    for i in range(1, len(factor)):
        candidate = float(factor.iloc[i])
        prev_close = float(close.iloc[i - 1])
        curr_close = float(close.iloc[i])
        raw_ratio = curr_close / prev_close if prev_close > 0 else 1.0

        large_up = candidate > current * 1.5
        large_down = candidate < current / 1.5

        if pending is not None:
            if raw_ratio < 0.7 or raw_ratio > 1.5:
                current = pending
                pending = None
            elif not (candidate > current * 1.5 or candidate < current / 1.5):
                pending = None

        if pending is None:
            if large_up:
                if raw_ratio < 0.7:
                    current = candidate
                else:
                    pending = candidate
            elif large_down:
                if raw_ratio > 1.5:
                    current = candidate
                else:
                    pending = candidate
            else:
                current = candidate

        aligned.append(current)

    return pd.Series(aligned, index=split_factor.index)


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


def _compute_adj_close(
    df: pd.DataFrame,
    nav_df: pd.DataFrame = None,
    code: str | None = None,
) -> pd.DataFrame:
    """Compute adj_close using pinned split factors (preferred) or NAV-derived factors.

    Priority:
      1. Pinned factors from split_factors.yaml (deterministic, immune to API drift)
      2. NAV-derived factors via cum_nav/nav (only for ETFs without pinned splits)
      3. Fallback: adj_close = close (no split information available)

    split_factor(date) = cum_nav(date) / nav(date)
    adj_close(date) = close(date) * split_factor(date)
    """
    df = df.copy()

    # --- Priority 1: Use pinned split factors if available ---
    pinned = PINNED_SPLITS.get(code, []) if code else []
    if pinned:
        prices = df.drop(columns=["adj_close"], errors="ignore").copy()
        prices["datetime"] = _to_utc_ns(prices["datetime"])
        # Build factor series from pinned events (sorted by event_date)
        events = sorted(pinned, key=lambda e: e["event_date"])
        factor_series = pd.Series(1.0, index=prices.index)
        for event in events:
            event_ts = pd.Timestamp(event["event_date"]).tz_convert("UTC")
            mask = prices["datetime"] >= event_ts
            factor_series[mask] = event["factor"]
            factor_series[~mask] = event["pre_factor"]
        prices["adj_close"] = prices["close"] * factor_series
        return prices

    # --- Priority 2: NAV-derived factors ---
    if nav_df is not None and "cum_nav" in nav_df.columns and "nav" in nav_df.columns:
        valid = nav_df.dropna(subset=["nav", "cum_nav"]).copy()
        valid = valid[valid["nav"] > 0]
        if not valid.empty:
            prices = df.drop(columns=["adj_close"], errors="ignore").copy()
            prices["datetime"] = _to_utc_ns(prices["datetime"])
            valid["datetime"] = _to_utc_ns(valid["datetime"])
            valid["split_factor"] = valid["cum_nav"] / valid["nav"]

            adjusted = pd.merge_asof(
                prices.sort_values("datetime"),
                valid[["datetime", "split_factor"]].sort_values("datetime"),
                on="datetime",
                direction="backward",
            )
            adjusted["split_factor"] = adjusted["split_factor"].ffill().fillna(1.0)
            adjusted["split_factor"] = _align_split_factor_to_price_jumps(
                adjusted["close"],
                adjusted["split_factor"],
            )
            adjusted["adj_close"] = adjusted["close"] * adjusted["split_factor"]
            return adjusted.drop(columns=["split_factor"])

    # --- Priority 3: Fallback ---
    df["adj_close"] = df["close"]
    return df


def _check_adj_close_regression(
    existing: pd.DataFrame,
    recomputed: pd.DataFrame,
    code: str,
) -> None:
    """Fail-closed guard: reject if recomputed adj_close diverges from existing.

    Compares overlap rows (same datetime) between existing and recomputed.
    If any row's adj_close changes by more than ADJ_CLOSE_DRIFT_TOLERANCE,
    raises ValueError to prevent writing corrupted data.
    """
    if existing.empty or "adj_close" not in existing.columns:
        return

    existing_cp = existing[["datetime", "adj_close"]].copy()
    existing_cp["datetime"] = _to_utc_ns(existing_cp["datetime"])
    recomputed_cp = recomputed[["datetime", "adj_close"]].copy()
    recomputed_cp["datetime"] = _to_utc_ns(recomputed_cp["datetime"])

    merged = existing_cp.merge(
        recomputed_cp, on="datetime", suffixes=("_old", "_new")
    )
    if merged.empty:
        return

    # Only check rows where old adj_close is meaningful (not zero/nan)
    valid = merged[merged["adj_close_old"].abs() > 1e-6].copy()
    if valid.empty:
        return

    valid["drift"] = (
        (valid["adj_close_new"] - valid["adj_close_old"]).abs()
        / valid["adj_close_old"]
    )
    max_drift = valid["drift"].max()
    if max_drift > ADJ_CLOSE_DRIFT_TOLERANCE:
        worst = valid.loc[valid["drift"].idxmax()]
        raise ValueError(
            f"{LOG_PREFIX} REGRESSION GUARD: {code} adj_close drift {max_drift:.1%} "
            f"exceeds {ADJ_CLOSE_DRIFT_TOLERANCE:.0%} tolerance. "
            f"Worst row: {worst['datetime']} old={worst['adj_close_old']:.4f} "
            f"new={worst['adj_close_new']:.4f}. "
            f"Refusing to write. Check NAV data or update split_factors.yaml."
        )


def _merge_existing_daily(
    existing: pd.DataFrame,
    fetched: pd.DataFrame,
    nav_df: pd.DataFrame | None,
    code: str | None = None,
) -> pd.DataFrame:
    """Merge refetched overlap rows and recompute adj_close for the full file."""
    if existing.empty:
        combined = fetched.copy()
    else:
        combined = pd.concat([existing, fetched], ignore_index=True)
        combined["datetime"] = _to_utc_ns(combined["datetime"])
        combined = combined.drop_duplicates(subset=["datetime"], keep="last")

    combined = combined.sort_values("datetime").reset_index(drop=True)
    if nav_df is not None and {"nav", "cum_nav"}.issubset(nav_df.columns):
        return _compute_adj_close(combined, nav_df=nav_df, code=code)
    if code and PINNED_SPLITS.get(code):
        return _compute_adj_close(combined, code=code)

    if "adj_close" not in combined.columns:
        combined["adj_close"] = combined["close"]
    else:
        combined["adj_close"] = combined["adj_close"].fillna(combined["close"])
    return combined


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

    if not existing.empty:
        new_rows = df[df["datetime"] > last_date]
        new_count = len(new_rows)
    else:
        new_count = len(df)

    combined = _merge_existing_daily(existing, df, nav_df, code=code)

    # Regression guard: reject if existing adj_close would be corrupted
    _check_adj_close_regression(existing, combined, code)

    combined.to_csv(csv_path, index=False)
    return new_count


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
