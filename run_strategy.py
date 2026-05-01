#!/usr/bin/env python3
"""Main runner script - fetches data, runs backtest, generates reports.

This script is designed to run via GitHub Actions on a 4H cron schedule.
It produces:
  - data/btc_ma240_4d/latest.json  (current strategy status)
  - data/btc_ma240_4d/backtest.json (backtest results)
  - data/btc_ma240_4d/charts/equity.png
  - data/btc_ma240_4d/charts/price_ma.png
"""

import shutil
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from strategies.btc_ma_trend.signal import StrategyConfig, compute_signals
from pipeline.data_fetcher import fetch_candles, fetch_historical_candles, load_local_history
from pipeline.backtest import run_backtest, compute_period_metrics
from pipeline.report import (
    generate_status_json,
    generate_backtest_json,
    generate_equity_chart,
    generate_price_ma_chart,
)

import pandas as pd

OUTPUT_DIR = Path("data/btc_ma240_4d")
CHARTS_DIR = OUTPUT_DIR / "charts"
PUBLIC_CHARTS_DIR = Path("site/public/charts")
FALLBACK_HISTORY_CANDLES = 6000
LOG_PREFIX = "[Quant Strategy]"

# Fixed launch date — "Since" reference is the last signal before this date.
# Once set per strategy, never changes.
LAUNCH_DATE = pd.Timestamp("2026-04-30", tz="UTC")


def load_strategy_data(config: StrategyConfig) -> pd.DataFrame:
    """Load enough candles for reproducible backtests in local and CI environments."""
    df_hist = None
    try:
        df_hist = load_local_history()
        print(f"{LOG_PREFIX} Loaded local history: {len(df_hist)} candles, "
              f"{df_hist.iloc[0]['timestamp']} to {df_hist.iloc[-1]['timestamp']}")
    except Exception as e:
        print(f"{LOG_PREFIX} Local history not available: {e}")

    if df_hist is None:
        print(f"{LOG_PREFIX} Fetching extended historical candles from OKX...")
        try:
            df_hist = fetch_historical_candles(
                symbol=config.symbol,
                bar=config.timeframe,
                limit=FALLBACK_HISTORY_CANDLES,
            )
            print(f"{LOG_PREFIX} Got {len(df_hist)} historical candles from OKX")
            return df_hist
        except Exception as e:
            print(f"{LOG_PREFIX} Historical fetch failed: {e}")

    print(f"{LOG_PREFIX} Fetching recent candles from OKX...")
    try:
        df_recent = fetch_candles(symbol=config.symbol, bar=config.timeframe, limit=300)
        print(f"{LOG_PREFIX} Got {len(df_recent)} recent candles from OKX")
    except Exception as e:
        print(f"{LOG_PREFIX} OKX fetch failed: {e}")
        df_recent = None

    if df_hist is not None and df_recent is not None:
        df = pd.concat([df_hist, df_recent], ignore_index=True)
        return df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    if df_hist is not None:
        return df_hist
    if df_recent is not None:
        return df_recent

    print(f"{LOG_PREFIX} ERROR: No data available")
    sys.exit(1)


def price_path_from_period(df: pd.DataFrame, period_start: pd.Timestamp) -> pd.Series:
    """Return B&H price path with a baseline at or immediately before period_start."""
    mask = df["timestamp"] <= period_start
    start_idx = int(mask[mask].index[-1]) if mask.any() else 0
    return df.loc[start_idx:, "close"]


def sync_public_charts(charts_dir: Path = CHARTS_DIR, public_charts_dir: Path = PUBLIC_CHARTS_DIR) -> None:
    """Copy generated chart assets to Astro's public directory."""
    public_charts_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("equity.png", "price_ma.png"):
        shutil.copyfile(charts_dir / filename, public_charts_dir / filename)


def main():
    config = StrategyConfig(
        ma_window=240,
        min_hold_bars=24,
        timeframe="4H",
        symbol="BTC-USDT",
    )

    print(f"{LOG_PREFIX} Running strategy: {config.display_name}")

    df = load_strategy_data(config)
    print(f"{LOG_PREFIX} Combined: {len(df)} candles, {df.iloc[0]['timestamp']} to {df.iloc[-1]['timestamp']}")

    # 2. Run backtest
    print(f"{LOG_PREFIX} Running backtest...")
    result = run_backtest(df, config)

    print(f"{LOG_PREFIX} Backtest complete:")
    print(f"  Trades: {result.total_trades}")
    print(f"  Return: {result.total_return_pct:.2f}%")
    print(f"  Max DD: {result.max_drawdown_pct:.2f}%")
    print(f"  B&H Max DD: {result.buy_hold_max_drawdown_pct:.2f}%")
    print(f"  Win Rate: {result.win_rate:.1f}%")
    print(f"  Sharpe: {result.sharpe_ratio:.3f}")
    print(f"  Buy & Hold: {result.buy_hold_return_pct:.2f}%")

    # 3. Determine current position from signal sequence
    signals = compute_signals(df, config)

    in_position = False
    entry_bar_idx = 0
    if signals and signals[-1].action == "buy":
        in_position = True
        df_sorted = df.sort_values("timestamp").reset_index(drop=True)
        mask = df_sorted["timestamp"] == signals[-1].timestamp
        if mask.any():
            entry_bar_idx = mask.idxmax()

    # 4. Compute "Since" date and period-filtered performance
    since_date = None
    for sig in reversed(signals):
        if sig.timestamp <= LAUNCH_DATE:
            since_date = sig.timestamp
            break

    if since_date:
        print(f"{LOG_PREFIX} Since date (last signal before launch): {since_date}")

    # Open position info for period metrics
    open_entry_time = None
    open_entry_price = 0.0
    if in_position and signals and signals[-1].action == "buy":
        open_entry_time = signals[-1].timestamp
        open_entry_price = signals[-1].price

    df_sorted = df.sort_values("timestamp").reset_index(drop=True)
    end_price = df_sorted.iloc[-1]["close"]
    end_date = df_sorted.iloc[-1]["timestamp"]

    period_boundaries = {}
    if since_date:
        period_boundaries["since_launch"] = since_date
    period_boundaries["1y"] = end_date - pd.DateOffset(years=1)
    period_boundaries["2y"] = end_date - pd.DateOffset(years=2)
    all_start_idx = config.ma_window if len(df_sorted) > config.ma_window else 0
    period_boundaries["all"] = df_sorted.iloc[all_start_idx]["timestamp"]

    periods_data = {}
    for name, p_start in period_boundaries.items():
        mask = df_sorted["timestamp"] <= p_start
        start_price = float(df_sorted.loc[mask, "close"].iloc[-1]) if mask.any() else float(df_sorted.iloc[0]["close"])

        metrics = compute_period_metrics(
            result.trades, p_start, end_price, start_price,
            open_entry_time, open_entry_price,
            equity_curve=result.equity_curve,
            benchmark_prices=price_path_from_period(df_sorted, p_start),
            timeframe=config.timeframe,
        )
        if metrics:
            periods_data[name] = {
                "start": p_start.isoformat(),
                "end": end_date.isoformat(),
                "performance": metrics,
            }

    print(f"{LOG_PREFIX} Period metrics computed: {list(periods_data.keys())}")

    # 5. Generate outputs
    print(f"{LOG_PREFIX} Generating reports...")

    generate_status_json(df, config, in_position, entry_bar_idx, OUTPUT_DIR / "latest.json")
    generate_backtest_json(
        result, OUTPUT_DIR / "backtest.json",
        since_date=since_date.isoformat() if since_date else None,
        periods=periods_data,
    )
    generate_equity_chart(result, CHARTS_DIR / "equity.png")
    generate_price_ma_chart(df, config, result.trades, CHARTS_DIR / "price_ma.png")
    sync_public_charts()

    print(f"{LOG_PREFIX} Reports written to {OUTPUT_DIR}/")
    print(f"{LOG_PREFIX} Done.")


if __name__ == "__main__":
    main()
