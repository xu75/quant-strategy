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

from strategies.btc_ma_trend.signal import StrategyConfig
from pipeline.data_fetcher import fetch_candles, load_local_history
from pipeline.backtest import run_backtest
from pipeline.report import (
    generate_status_json,
    generate_backtest_json,
    generate_equity_chart,
    generate_price_ma_chart,
)


OUTPUT_DIR = Path("data/btc_ma240_4d")
CHARTS_DIR = OUTPUT_DIR / "charts"


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
    import pandas as pd
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

    # 3. Determine current position from signal sequence (not trades)
    # Signal sequence is the single source of truth for position state.
    # If the last signal is "buy" with no subsequent "sell", we're in position.
    from strategies.btc_ma_trend.signal import compute_signals
    signals = compute_signals(df, config)

    in_position = False
    entry_bar_idx = 0
    if signals and signals[-1].action == "buy":
        in_position = True
        df_sorted = df.sort_values("timestamp").reset_index(drop=True)
        mask = df_sorted["timestamp"] == signals[-1].timestamp
        if mask.any():
            entry_bar_idx = mask.idxmax()

    # 4. Generate outputs
    print("[MeshHub] Generating reports...")

    generate_status_json(df, config, in_position, entry_bar_idx, OUTPUT_DIR / "latest.json")
    generate_backtest_json(result, OUTPUT_DIR / "backtest.json")
    generate_equity_chart(result, CHARTS_DIR / "equity.png")
    generate_price_ma_chart(df, config, result.trades, CHARTS_DIR / "price_ma.png")

    print(f"[MeshHub] Reports written to {OUTPUT_DIR}/")
    print("[MeshHub] Done.")


if __name__ == "__main__":
    main()
