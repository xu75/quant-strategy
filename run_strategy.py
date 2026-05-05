#!/usr/bin/env python3
"""Main runner script - orchestrates all enabled strategies.

This script is designed to run via GitHub Actions on a 4H cron schedule.
It discovers all enabled strategies and runs them through the pipeline.

Modes:
  update-data    — fetch new candles and append to canonical CSVs in data/market/
  daily-signal   — incremental signal update from persisted state
  full-backtest  — complete historical backtest, updates all outputs
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

VALID_MODES = ("update-data", "full-backtest", "daily-signal")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Quant strategy pipeline")
    parser.add_argument(
        "--mode",
        choices=VALID_MODES,
        default="full-backtest",
        help="Pipeline mode: update-data, daily-signal, or full-backtest (default)",
    )
    args = parser.parse_args()

    if args.mode == "update-data":
        from pipeline.update_market_data import run_update
        run_update()
    else:
        from core.runner import run_all_strategies
        run_all_strategies(mode=args.mode)
