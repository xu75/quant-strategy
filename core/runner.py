from __future__ import annotations

"""Strategy runner - orchestrates data fetch, backtest, and report generation.

Discovers all enabled strategies and runs them through the pipeline.
"""

import shutil
import sys
from pathlib import Path

import pandas as pd

from core.registry import discover_strategies, load_strategy_module
from pipeline.data_fetcher import fetch_candles, fetch_historical_candles, load_local_history, load_local_history_by_name
from pipeline.backtest import run_backtest, compute_period_metrics
from pipeline.report import (
    generate_status_json,
    generate_backtest_json,
    generate_equity_chart,
    generate_price_ma_chart,
)


OUTPUT_BASE_DIR = Path("data")
SITE_PUBLIC_CHARTS = Path("site/public/charts")
FALLBACK_HISTORY_CANDLES = 6000
LOG_PREFIX = "[Quant Strategy]"


def load_strategy_data(manifest, config) -> pd.DataFrame:
    """Load enough candles for reproducible backtests."""
    # Check if manifest declares a local file for the primary symbol
    raw = getattr(manifest, '_raw_data', None)
    if raw:
        data_sources = raw.get("data_sources", {})
        primary_symbol = manifest.config.get("symbol", "")
        for src in data_sources.values():
            if src.get("symbol") == primary_symbol and src.get("local_file"):
                local_file = src["local_file"]
                timeframe = src.get("timeframe", config.timeframe)
                print(f"{LOG_PREFIX} [{manifest.id}] Loading primary data from local file: {local_file}")
                df = load_local_history_by_name(local_file, target_bar=timeframe)
                print(
                    f"{LOG_PREFIX} [{manifest.id}] Loaded {len(df)} candles, "
                    f"{df.iloc[0]['timestamp']} to {df.iloc[-1]['timestamp']}"
                )
                return df

    df_hist = None
    try:
        df_hist = load_local_history(target_bar=config.timeframe)
        print(
            f"{LOG_PREFIX} [{manifest.id}] Loaded local history: {len(df_hist)} candles, "
            f"{df_hist.iloc[0]['timestamp']} to {df_hist.iloc[-1]['timestamp']}"
        )
    except Exception as e:
        print(f"{LOG_PREFIX} [{manifest.id}] Local history not available: {e}")

    if df_hist is None:
        print(f"{LOG_PREFIX} [{manifest.id}] Fetching extended historical candles from OKX...")
        try:
            df_hist = fetch_historical_candles(
                symbol=config.symbol,
                bar=config.timeframe,
                limit=FALLBACK_HISTORY_CANDLES,
            )
            print(f"{LOG_PREFIX} [{manifest.id}] Got {len(df_hist)} historical candles from OKX")
            return df_hist
        except Exception as e:
            print(f"{LOG_PREFIX} [{manifest.id}] Historical fetch failed: {e}")

    print(f"{LOG_PREFIX} [{manifest.id}] Fetching recent candles from OKX...")
    try:
        df_recent = fetch_candles(symbol=config.symbol, bar=config.timeframe, limit=300)
        print(f"{LOG_PREFIX} [{manifest.id}] Got {len(df_recent)} recent candles from OKX")
    except Exception as e:
        print(f"{LOG_PREFIX} [{manifest.id}] OKX fetch failed: {e}")
        df_recent = None

    if df_hist is not None and df_recent is not None:
        df = pd.concat([df_hist, df_recent], ignore_index=True)
        return df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    if df_hist is not None:
        return df_hist
    if df_recent is not None:
        return df_recent

    print(f"{LOG_PREFIX} [{manifest.id}] ERROR: No data available")
    sys.exit(1)


def load_extra_data_sources(manifest) -> dict[str, pd.DataFrame]:
    """Load additional data sources declared in manifest.data_sources.

    Returns a dict mapping source key (e.g. 'btc') to DataFrame.
    Skips the primary source (matching config.symbol) since the runner
    loads that via load_strategy_data.
    """
    raw = getattr(manifest, '_raw_data', None)
    if raw is None:
        return {}

    data_sources = raw.get("data_sources", {})
    if not data_sources:
        return {}

    primary_symbol = manifest.config.get("symbol", "")
    extras = {}

    for key, src in data_sources.items():
        if src.get("symbol") == primary_symbol:
            continue
        local_file = src.get("local_file")
        timeframe = src.get("timeframe", "1H")
        if local_file:
            print(f"{LOG_PREFIX} [{manifest.id}] Loading extra source '{key}': {local_file} @ {timeframe}")
            extras[key] = load_local_history_by_name(local_file, target_bar=timeframe)
            print(f"{LOG_PREFIX} [{manifest.id}]   -> {len(extras[key])} candles")

    return extras


def price_path_from_period(df: pd.DataFrame, period_start: pd.Timestamp) -> pd.Series:
    """Return B&H price path with a baseline at or immediately before period_start."""
    mask = df["timestamp"] <= period_start
    start_idx = int(mask[mask].index[-1]) if mask.any() else 0
    return df.loc[start_idx:, "close"]


def build_performance_period_boundaries(
    end_date: pd.Timestamp,
    since_date: pd.Timestamp | None = None,
) -> dict[str, pd.Timestamp]:
    """Return the product-standard performance windows for strategy summaries."""
    boundaries = {}
    if since_date is not None:
        boundaries["since_launch"] = since_date
    for years in (1, 2, 3, 5):
        boundaries[f"{years}y"] = end_date - pd.DateOffset(years=years)
    return boundaries


def sync_public_charts(strategy_id: str, charts_dir: Path) -> None:
    """Copy generated chart assets to Astro's public directory with strategy namespace."""
    public_strategy_charts = SITE_PUBLIC_CHARTS / strategy_id
    public_strategy_charts.mkdir(parents=True, exist_ok=True)
    for filename in ("equity.png", "price_ma.png"):
        src = charts_dir / filename
        if src.exists():
            shutil.copyfile(src, public_strategy_charts / filename)


def run_single_strategy(adapter):
    """Run a single strategy through the full pipeline."""
    manifest = adapter.manifest
    config = adapter.config

    print(f"\n{LOG_PREFIX} ========================================")
    print(f"{LOG_PREFIX} Running strategy: {manifest.name} ({manifest.id})")
    print(f"{LOG_PREFIX} ========================================")

    # 1. Load data
    df = load_strategy_data(manifest, config)
    extra_data = load_extra_data_sources(manifest)
    print(
        f"{LOG_PREFIX} [{manifest.id}] Combined: {len(df)} candles, "
        f"{df.iloc[0]['timestamp']} to {df.iloc[-1]['timestamp']}"
    )

    # 2. Compute signals (injected from strategy)
    # Multi-symbol strategies receive extra data as kwargs
    extra_kwargs = {}
    if "btc" in extra_data:
        extra_kwargs["btc_df"] = extra_data["btc"]
    signals = adapter.compute_signals(df, config, **extra_kwargs)

    # For strategies with data filtering (e.g. regular hours), use filtered df
    # so backtest and index spaces are consistent with signal timestamps
    df_backtest = df
    if adapter.get_filtered_df is not None:
        df_backtest = adapter.get_filtered_df(df, config)
        print(
            f"{LOG_PREFIX} [{manifest.id}] Filtered to {len(df_backtest)} candles for backtest"
        )

    # 3. Run backtest (pass pre-computed signals)
    print(f"{LOG_PREFIX} [{manifest.id}] Running backtest...")
    result = run_backtest(df_backtest, config, signals=signals, fee_rate=0.001)

    print(f"{LOG_PREFIX} [{manifest.id}] Backtest complete:")
    print(f"  Trades: {result.total_trades}")
    print(f"  Return: {result.total_return_pct:.2f}%")
    print(f"  Max DD: {result.max_drawdown_pct:.2f}%")
    print(f"  B&H Max DD: {result.buy_hold_max_drawdown_pct:.2f}%")
    print(f"  Win Rate: {result.win_rate:.1f}%")
    print(f"  Sharpe: {result.sharpe_ratio:.3f}")
    print(f"  Buy & Hold: {result.buy_hold_return_pct:.2f}%")

    # 4. Determine current position from signal sequence
    in_position = False
    entry_bar_idx = 0
    if signals and signals[-1].action == "buy":
        in_position = True
        df_sorted = df_backtest.sort_values("timestamp").reset_index(drop=True)
        mask = df_sorted["timestamp"] == signals[-1].timestamp
        if mask.any():
            entry_bar_idx = mask.idxmax()

    # 5. Compute "Since" date and period-filtered performance
    launch_date = pd.Timestamp(manifest.launch_date, tz="UTC")
    since_date = None
    for sig in reversed(signals):
        if sig.timestamp <= launch_date:
            since_date = sig.timestamp
            break

    if since_date:
        print(f"{LOG_PREFIX} [{manifest.id}] Since date (last signal before launch): {since_date}")

    # Open position info for period metrics
    open_entry_time = None
    open_entry_price = 0.0
    if in_position and signals and signals[-1].action == "buy":
        open_entry_time = signals[-1].timestamp
        open_entry_price = signals[-1].price

    df_sorted = df_backtest.sort_values("timestamp").reset_index(drop=True)
    end_price = df_sorted.iloc[-1]["close"]
    end_date = df_sorted.iloc[-1]["timestamp"]

    period_boundaries = build_performance_period_boundaries(end_date, since_date)

    periods_data = {}
    for name, p_start in period_boundaries.items():
        mask = df_sorted["timestamp"] <= p_start
        start_price = (
            float(df_sorted.loc[mask, "close"].iloc[-1])
            if mask.any()
            else float(df_sorted.iloc[0]["close"])
        )

        metrics = compute_period_metrics(
            result.trades,
            p_start,
            end_price,
            start_price,
            open_entry_time,
            open_entry_price,
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

    print(f"{LOG_PREFIX} [{manifest.id}] Period metrics computed: {list(periods_data.keys())}")

    # 6. Generate outputs
    print(f"{LOG_PREFIX} [{manifest.id}] Generating reports...")

    output_dir = OUTPUT_BASE_DIR / manifest.id
    charts_dir = output_dir / "charts"

    generate_status_json(
        df_backtest, config, in_position, entry_bar_idx, output_dir / "latest.json",
        current_signal=adapter.get_current_signal(df, in_position, entry_bar_idx, config, **extra_kwargs),
    )
    generate_backtest_json(
        result,
        output_dir / "backtest.json",
        since_date=since_date.isoformat() if since_date else None,
        periods=periods_data,
    )
    generate_equity_chart(result, charts_dir / "equity.png", benchmark_prices=df_sorted[["timestamp", "close"]])
    generate_price_ma_chart(df_backtest, config, result.trades, charts_dir / "price_ma.png")
    sync_public_charts(manifest.id, charts_dir)

    print(f"{LOG_PREFIX} [{manifest.id}] Reports written to {output_dir}/")
    print(f"{LOG_PREFIX} [{manifest.id}] Done.")


def run_all_strategies():
    """Main orchestration: discover strategies → run each → output reports."""
    print(f"{LOG_PREFIX} Discovering strategies...")
    manifests = discover_strategies()

    if not manifests:
        print(f"{LOG_PREFIX} No enabled strategies found.")
        return

    print(f"{LOG_PREFIX} Found {len(manifests)} enabled strategies:")
    for m in manifests:
        print(f"  - {m.name} ({m.id})")

    failed = []
    for manifest in manifests:
        try:
            adapter = load_strategy_module(manifest)
            run_single_strategy(adapter)
        except Exception as e:
            print(f"{LOG_PREFIX} [{manifest.id}] ERROR: {e}")
            import traceback
            traceback.print_exc()
            failed.append(manifest.id)

    if failed:
        print(f"\n{LOG_PREFIX} FAILED strategies: {failed}")
        sys.exit(1)

    print(f"\n{LOG_PREFIX} All strategies complete.")
