#!/usr/bin/env python3
"""Main runner script - fetches data, runs backtest, generates reports.

This script is designed to run via GitHub Actions on a 4H cron schedule.
It produces:
  - data/btc_ma240_4d/latest.json  (current strategy status)
  - data/btc_ma240_4d/backtest.json (backtest results)
  - data/btc_ma240_4d/charts/equity.png
  - data/btc_ma240_4d/charts/price_ma.png
"""

import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from strategies.btc_ma_trend.signal import StrategyConfig, compute_signals
from pipeline.data_fetcher import fetch_candles, load_local_history
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

# Fixed launch date — "Since" reference is the last signal before this date.
# Once set per strategy, never changes.
LAUNCH_DATE = pd.Timestamp("2026-04-30", tz="UTC")


def main():
    config = StrategyConfig(
        ma_window=240,
        min_hold_bars=24,
        timeframe="4H",
        symbol="BTC-USDT",
    )

    print(f"[MeshHub] Running strategy: BTC {config.timeframe} MA{config.ma_window}")

    # 1. Load local historical data (resampled 1h→4h)
    df_hist = None
    try:
        df_hist = load_local_history()
        print(f"[MeshHub] Loaded local history: {len(df_hist)} candles, "
              f"{df_hist.iloc[0]['timestamp']} to {df_hist.iloc[-1]['timestamp']}")
    except Exception as e:
        print(f"[MeshHub] Local history not available: {e}")

    # 2. Fetch recent data from OKX (forward updates)
    print("[MeshHub] Fetching recent candles from OKX...")
    try:
        df_recent = fetch_candles(symbol=config.symbol, bar=config.timeframe, limit=300)
        print(f"[MeshHub] Got {len(df_recent)} recent candles from OKX")
    except Exception as e:
        print(f"[MeshHub] OKX fetch failed: {e}")
        df_recent = None

    # 3. Combine: local history + OKX recent (dedup by timestamp)
    if df_hist is not None and df_recent is not None:
        df = pd.concat([df_hist, df_recent], ignore_index=True)
        df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    elif df_hist is not None:
        df = df_hist
    elif df_recent is not None:
        df = df_recent
    else:
        print("[MeshHub] ERROR: No data available")
        sys.exit(1)

    print(f"[MeshHub] Combined: {len(df)} candles, {df.iloc[0]['timestamp']} to {df.iloc[-1]['timestamp']}")

    # 2. Run backtest
    print("[MeshHub] Running backtest...")
    result = run_backtest(df, config)

    print(f"[MeshHub] Backtest complete:")
    print(f"  Trades: {result.total_trades}")
    print(f"  Return: {result.total_return_pct:.2f}%")
    print(f"  Max DD: {result.max_drawdown_pct:.2f}%")
    print(f"  Win Rate: {result.win_rate:.1f}%")
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
        print(f"[MeshHub] Since date (last signal before launch): {since_date}")

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
    period_boundaries["all"] = df_sorted.iloc[0]["timestamp"]

    periods_data = {}
    for name, p_start in period_boundaries.items():
        mask = df_sorted["timestamp"] <= p_start
        start_price = float(df_sorted.loc[mask, "close"].iloc[-1]) if mask.any() else float(df_sorted.iloc[0]["close"])

        metrics = compute_period_metrics(
            result.trades, p_start, end_price, start_price,
            open_entry_time, open_entry_price,
        )
        if metrics:
            periods_data[name] = {
                "start": p_start.isoformat(),
                "end": end_date.isoformat(),
                "performance": metrics,
            }

    print(f"[MeshHub] Period metrics computed: {list(periods_data.keys())}")

    # 5. Generate outputs
    print("[MeshHub] Generating reports...")

    generate_status_json(df, config, in_position, entry_bar_idx, OUTPUT_DIR / "latest.json")
    generate_backtest_json(
        result, OUTPUT_DIR / "backtest.json",
        since_date=since_date.isoformat() if since_date else None,
        periods=periods_data,
    )
    generate_equity_chart(result, CHARTS_DIR / "equity.png")
    generate_price_ma_chart(df, config, result.trades, CHARTS_DIR / "price_ma.png")

    print(f"[MeshHub] Reports written to {OUTPUT_DIR}/")
    print("[MeshHub] Done.")


if __name__ == "__main__":
    main()
