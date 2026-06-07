from __future__ import annotations

"""Strategy runner - orchestrates data fetch, backtest, and report generation.

Discovers all enabled strategies and runs them through the pipeline.
"""

import shutil
import sys
from pathlib import Path

import pandas as pd

from core.registry import discover_strategies, load_strategy_module
from core.state import config_hash, load_state, save_state, validate_state_for_resume
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

DISPLAY_POSITION_THRESHOLD = 0.03


def is_display_in_position(exposure: float | None, fallback: bool = False) -> bool:
    """Determine if position should display as 'in position' for latest.json.

    Uses a higher threshold than backtest semantics (0.01) to avoid showing
    residual positions (<=3%) as actively held.
    """
    if exposure is None:
        return fallback
    return exposure > DISPLAY_POSITION_THRESHOLD


def _hours_per_bar(timeframe: str) -> float:
    tf = timeframe.strip().upper()
    if tf.endswith("H"):
        return float(tf[:-1])
    if tf.endswith("D"):
        return float(tf[:-1]) * 24
    return 4.0


def validate_data_coverage(
    df: pd.DataFrame,
    min_lookback_years: int,
    warmup_bars: int,
    timeframe: str,
) -> None:
    """Validate that data covers min_lookback_years + warmup. Raises ValueError if not."""
    if df.empty:
        raise ValueError("coverage: empty DataFrame")

    data_start = pd.Timestamp(df.iloc[0]["timestamp"])
    if data_start.tzinfo is None:
        data_start = data_start.tz_localize("UTC")

    data_end = pd.Timestamp(df.iloc[-1]["timestamp"])
    if data_end.tzinfo is None:
        data_end = data_end.tz_localize("UTC")

    warmup_hours = warmup_bars * _hours_per_bar(timeframe)
    required_start = data_end - pd.DateOffset(years=min_lookback_years) - pd.Timedelta(hours=warmup_hours)

    if data_start > required_start:
        raise ValueError(
            f"coverage: data starts {data_start.date()}, but need {required_start.date()} "
            f"({min_lookback_years}y + {warmup_bars} bars warmup). "
            f"Got {len(df)} rows covering {data_start.date()} to {data_end.date()}."
        )


def load_strategy_data(manifest, config, canonical_only: bool = False) -> pd.DataFrame:
    """Load enough candles for reproducible backtests.

    If manifest declares data_sources with a local_file for the primary symbol,
    that canonical source is required. OKX/yfinance fallback is only allowed
    when no canonical source is declared (legacy mode — will fail coverage check).

    canonical_only: If True, reject legacy local paths — only data/market/ is accepted.
    """
    raw = getattr(manifest, '_raw_data', None)
    if raw:
        data_sources = raw.get("data_sources", {})
        primary_symbol = manifest.config.get("symbol", "")
        for src in data_sources.values():
            if src.get("symbol") == primary_symbol and src.get("local_file"):
                local_file = src["local_file"]
                timeframe = src.get("timeframe", config.timeframe)
                print(f"{LOG_PREFIX} [{manifest.id}] Loading primary data from local file: {local_file}")
                df = load_local_history_by_name(local_file, target_bar=timeframe, canonical_only=canonical_only)
                print(
                    f"{LOG_PREFIX} [{manifest.id}] Loaded {len(df)} candles, "
                    f"{df.iloc[0]['timestamp']} to {df.iloc[-1]['timestamp']}"
                )
                return df
        if data_sources:
            raise ValueError(
                f"[{manifest.id}] data_sources declared but no local_file for primary symbol '{primary_symbol}' — "
                f"full-backtest requires canonical data"
            )

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


def load_extra_data_sources(manifest, canonical_only: bool = False) -> dict[str, pd.DataFrame]:
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
    primary_timeframe = manifest.config.get("timeframe", "1H")
    extras = {}

    for key, src in data_sources.items():
        if src.get("symbol") == primary_symbol and src.get("timeframe", "1H") == primary_timeframe:
            continue
        local_file = src.get("local_file")
        timeframe = src.get("timeframe", "1H")
        if local_file:
            print(f"{LOG_PREFIX} [{manifest.id}] Loading extra source '{key}': {local_file} @ {timeframe}")
            extras[key] = load_local_history_by_name(local_file, target_bar=timeframe, canonical_only=canonical_only)
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


def filter_periods_by_coverage(
    boundaries: dict[str, pd.Timestamp],
    data_start: pd.Timestamp,
) -> dict[str, pd.Timestamp]:
    """Filter period boundaries to only those covered by data_start."""
    return {
        name: ts for name, ts in boundaries.items()
        if data_start <= ts
    }


def sync_public_charts(strategy_id: str, charts_dir: Path) -> None:
    """Copy generated chart assets to Astro's public directory with strategy namespace."""
    public_strategy_charts = SITE_PUBLIC_CHARTS / strategy_id
    public_strategy_charts.mkdir(parents=True, exist_ok=True)
    for filename in ("equity.png", "price_ma.png"):
        src = charts_dir / filename
        if src.exists():
            shutil.copyfile(src, public_strategy_charts / filename)


def run_single_strategy(adapter, mode: str = "full-backtest"):
    """Run a single strategy through the pipeline.

    mode: "full-backtest" — complete historical backtest, updates all outputs
          "daily-signal"  — incremental from persisted state, updates latest.json + state.json only
    """
    if mode == "daily-signal":
        return _run_daily_signal(adapter)
    return _run_full_backtest(adapter)


def _run_full_backtest(adapter):
    """Full backtest mode — the original pipeline."""
    manifest = adapter.manifest
    config = adapter.config

    print(f"\n{LOG_PREFIX} ========================================")
    print(f"{LOG_PREFIX} Running strategy: {manifest.name} ({manifest.id})")
    print(f"{LOG_PREFIX} ========================================")

    # 1. Load data (canonical_only in full-backtest to prevent legacy fallback)
    df = load_strategy_data(manifest, config, canonical_only=True)
    extra_data = load_extra_data_sources(manifest, canonical_only=True)
    print(
        f"{LOG_PREFIX} [{manifest.id}] Combined: {len(df)} candles, "
        f"{df.iloc[0]['timestamp']} to {df.iloc[-1]['timestamp']}"
    )

    # 1b. Coverage validation (fail-closed)
    raw = getattr(manifest, '_raw_data', None) or {}
    min_lookback = raw.get("min_lookback_years")
    warmup = raw.get("warmup_bars")
    if min_lookback and warmup:
        validate_data_coverage(df, min_lookback, warmup, config.timeframe)
        print(f"{LOG_PREFIX} [{manifest.id}] Coverage OK (primary): {min_lookback}y + {warmup} bars warmup")

        # Compute wall-clock required_start from primary data
        # All extra sources must cover at least this same start date
        primary_end = pd.Timestamp(df.iloc[-1]["timestamp"])
        if primary_end.tzinfo is None:
            primary_end = primary_end.tz_localize("UTC")
        warmup_hours = warmup * _hours_per_bar(config.timeframe)
        required_start = primary_end - pd.DateOffset(years=min_lookback) - pd.Timedelta(hours=warmup_hours)

        # Validate extra data sources against the same wall-clock boundary
        data_sources = raw.get("data_sources", {})
        primary_symbol = manifest.config.get("symbol", "")
        primary_timeframe = manifest.config.get("timeframe", "1H")
        for key, src in data_sources.items():
            if src.get("symbol") == primary_symbol and src.get("timeframe", "1H") == primary_timeframe:
                continue
            if key in extra_data:
                edf = extra_data[key]
                src_start = pd.Timestamp(edf.iloc[0]["timestamp"])
                if src_start.tzinfo is None:
                    src_start = src_start.tz_localize("UTC")
                if src_start > required_start:
                    raise ValueError(
                        f"coverage: extra source '{key}' starts {src_start.date()}, "
                        f"but need {required_start.date()} "
                        f"(same wall-clock boundary as primary: {min_lookback}y + {warmup} bars @ {config.timeframe})"
                    )
                print(f"{LOG_PREFIX} [{manifest.id}] Coverage OK (extra '{key}'): starts {src_start.date()}")

    # 2. Compute signals and run backtest
    # Strategies with run_backtest use their own engine (e.g. continuous exposure).
    # Others use the platform's binary buy/sell backtest.
    if adapter.run_backtest is not None:
        print(f"{LOG_PREFIX} [{manifest.id}] Running engine-native backtest...")
        result = adapter.run_backtest(df, config, extra_data=extra_data)
        signals = adapter.compute_signals(df, config, extra_data=extra_data)

        df_backtest = df
        if adapter.get_filtered_df is not None:
            df_backtest = adapter.get_filtered_df(df, config)
            print(
                f"{LOG_PREFIX} [{manifest.id}] Filtered to {len(df_backtest)} candles for backtest"
            )
    else:
        signals = adapter.compute_signals(df, config, extra_data=extra_data)

        df_backtest = df
        if adapter.get_filtered_df is not None:
            df_backtest = adapter.get_filtered_df(df, config)
            print(
                f"{LOG_PREFIX} [{manifest.id}] Filtered to {len(df_backtest)} candles for backtest"
            )

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

    # 4. Determine current position state
    if adapter.run_backtest is not None:
        in_position = result.has_open_position
        entry_bar_idx = 0
    else:
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
    # Engine-native strategies embed unrealized P&L in the equity curve,
    # so we skip the binary open-position tracking for them.
    open_entry_time = None
    open_entry_price = 0.0
    if adapter.run_backtest is None and in_position and signals and signals[-1].action == "buy":
        open_entry_time = signals[-1].timestamp
        open_entry_price = signals[-1].price

    df_sorted = df_backtest.sort_values("timestamp").reset_index(drop=True)
    end_price = df_sorted.iloc[-1]["close"]
    end_date = df_sorted.iloc[-1]["timestamp"]
    if adapter.get_benchmark_prices is not None:
        benchmark_df = adapter.get_benchmark_prices(df, config, extra_data=extra_data)
        benchmark_df = benchmark_df.sort_values("timestamp").reset_index(drop=True)
    else:
        benchmark_df = df_sorted[["timestamp", "close"]]

    period_boundaries = build_performance_period_boundaries(end_date, since_date)

    periods_data = {}
    data_start = df_sorted.iloc[0]["timestamp"]
    if hasattr(data_start, 'tzinfo') and data_start.tzinfo is None:
        data_start = data_start.tz_localize("UTC")
    valid_boundaries = filter_periods_by_coverage(period_boundaries, data_start)
    skipped = set(period_boundaries.keys()) - set(valid_boundaries.keys())
    for name in skipped:
        print(f"{LOG_PREFIX} [{manifest.id}] Period '{name}' skipped: data starts after boundary")
    for name, p_start in valid_boundaries.items():
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
            benchmark_prices=price_path_from_period(benchmark_df, p_start),
            timeframe=config.timeframe,
            use_equity_curve_returns=adapter.run_backtest is not None,
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

    # Build provenance metadata (per-source)
    def _ts_iso(ts):
        return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)

    sources_provenance = []
    sources_provenance.append({
        "key": "primary",
        "symbol": manifest.config.get("symbol", ""),
        "timeframe": config.timeframe,
        "source": "canonical",
        "rows": len(df),
        "start": _ts_iso(df.iloc[0]["timestamp"]),
        "end": _ts_iso(df.iloc[-1]["timestamp"]),
    })
    for key, edf in extra_data.items():
        sources_provenance.append({
            "key": key,
            "symbol": raw.get("data_sources", {}).get(key, {}).get("symbol", ""),
            "timeframe": raw.get("data_sources", {}).get(key, {}).get("timeframe", "1H"),
            "source": "canonical",
            "rows": len(edf),
            "start": _ts_iso(edf.iloc[0]["timestamp"]),
            "end": _ts_iso(edf.iloc[-1]["timestamp"]),
        })

    provenance = {
        "generation_mode": "full-backtest",
        "coverage_ok": True,
        "min_lookback_years": raw.get("min_lookback_years"),
        "sources": sources_provenance,
    }

    current_signal = adapter.get_current_signal(df, in_position, entry_bar_idx, config, extra_data=extra_data)

    display_in_position = is_display_in_position(
        getattr(current_signal, 'target_exposure', None), fallback=in_position
    )

    generate_status_json(
        df_backtest, config, display_in_position, entry_bar_idx, output_dir / "latest.json",
        current_signal=current_signal,
    )
    generate_backtest_json(
        result,
        output_dir / "backtest.json",
        since_date=since_date.isoformat() if since_date else None,
        periods=periods_data,
        provenance=provenance,
    )
    generate_equity_chart(result, charts_dir / "equity.png", benchmark_prices=benchmark_df)
    # Build extra_series for multi-asset chart overlay (only secondary_symbol)
    chart_extra_series = None
    secondary_symbol = getattr(config, 'secondary_symbol', None)
    if secondary_symbol and extra_data:
        for key, edf in extra_data.items():
            if key.upper() == secondary_symbol.upper() or key == secondary_symbol.lower():
                if "timestamp" in edf.columns and "close" in edf.columns:
                    chart_extra_series = {secondary_symbol: edf[["timestamp", "close"]]}
                    break
    generate_price_ma_chart(df_backtest, config, result.trades, charts_dir / "price_ma.png", extra_series=chart_extra_series)
    sync_public_charts(manifest.id, charts_dir)

    # 7. Export engine state for future daily-signal runs
    if adapter.run_backtest is not None:
        _export_engine_state(adapter, df, config, extra_data, output_dir)

    print(f"{LOG_PREFIX} [{manifest.id}] Reports written to {output_dir}/")
    print(f"{LOG_PREFIX} [{manifest.id}] Done.")


def _export_engine_state(adapter, df, config, extra_data, output_dir):
    """Persist engine state snapshot for daily-signal resume.

    Requires the strategy module to expose export_engine_state(df, config, extra_data).
    """
    manifest = adapter.manifest
    export_fn = adapter.export_engine_state
    if export_fn is None:
        return

    snapshot = export_fn(df, config, extra_data=extra_data)
    snapshot["strategy_id"] = manifest.id
    snapshot["strategy_version"] = manifest.version
    snapshot["config_hash"] = config_hash(dict(manifest.config))
    snapshot["mode"] = "full-backtest"

    save_state(output_dir / "state.json", snapshot)
    print(f"{LOG_PREFIX} [{manifest.id}] Engine state exported (watermark={snapshot.get('watermark')})")


def _run_daily_signal(adapter):
    """Daily-signal mode — incremental update from persisted state.

    Only updates latest.json and state.json. Never touches backtest.json or charts.
    Fail-closed: refuses to run if state is missing, stale, or config changed.
    Strategies without incremental support are skipped (not fallback to full-backtest).
    """
    manifest = adapter.manifest
    config = adapter.config

    print(f"\n{LOG_PREFIX} ========================================")
    print(f"{LOG_PREFIX} Daily signal: {manifest.name} ({manifest.id})")
    print(f"{LOG_PREFIX} ========================================")

    # Only strategies with incremental support can run in daily-signal mode
    if adapter.run_incremental is None:
        print(f"{LOG_PREFIX} [{manifest.id}] SKIP: no incremental support (daily-signal requires run_incremental)")
        return

    output_dir = OUTPUT_BASE_DIR / manifest.id
    state_path = output_dir / "state.json"

    # Load persisted state
    saved = load_state(state_path)
    if saved is None:
        print(f"{LOG_PREFIX} [{manifest.id}] SKIP: no state.json found — run full-backtest first")
        return

    # Load data
    df = load_strategy_data(manifest, config)
    extra_data = load_extra_data_sources(manifest)

    df_sorted = df.sort_values("timestamp").reset_index(drop=True)
    latest_ts = pd.Timestamp(df_sorted.iloc[-1]["timestamp"])
    if latest_ts.tzinfo is None:
        latest_ts = latest_ts.tz_localize("UTC")

    # Validate state
    current_hash = config_hash(dict(manifest.config))
    warmup = config.__dict__.get("warmup_bars", 960)
    error = validate_state_for_resume(
        saved,
        strategy_id=manifest.id,
        strategy_version=manifest.version,
        current_config_hash=current_hash,
        latest_data_ts=latest_ts,
        warmup_bars=warmup,
    )
    if error:
        print(f"{LOG_PREFIX} [{manifest.id}] SKIP (fail-closed): {error}")
        return

    # Restore engine and run incremental bars
    incremental_result = adapter.run_incremental(df, config, saved, extra_data=extra_data)

    if incremental_result is None:
        print(f"{LOG_PREFIX} [{manifest.id}] No new bars since watermark")
        return

    new_watermark = incremental_result["watermark"]
    exposure = incremental_result["exposure"]
    mode_str = incremental_result["mode"]
    regime_str = incremental_result["regime"]
    snapshot = incremental_result["state"]
    current_signal = incremental_result["current_signal"]

    print(
        f"{LOG_PREFIX} [{manifest.id}] Signal: exposure={exposure:.3f}, "
        f"mode={mode_str}, regime={regime_str}"
    )

    # Determine position state (display: <=3% exposure = "空仓")
    in_position = is_display_in_position(exposure)
    entry_bar_idx = 0

    # Generate latest.json only — signal comes from incremental result, not full replay
    generate_status_json(
        df if adapter.get_filtered_df is None else adapter.get_filtered_df(df, config),
        config, in_position, entry_bar_idx, output_dir / "latest.json",
        current_signal=current_signal,
    )

    # Update state.json
    snapshot["strategy_id"] = manifest.id
    snapshot["strategy_version"] = manifest.version
    snapshot["config_hash"] = current_hash
    snapshot["mode"] = "daily-signal"
    save_state(state_path, snapshot)

    print(f"{LOG_PREFIX} [{manifest.id}] State updated (watermark={new_watermark})")
    print(f"{LOG_PREFIX} [{manifest.id}] Done (daily-signal).")


def run_all_strategies(mode: str = "full-backtest"):
    """Main orchestration: discover strategies → run each → output reports."""
    print(f"{LOG_PREFIX} Discovering strategies... (mode={mode})")
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
            run_single_strategy(adapter, mode=mode)
        except Exception as e:
            print(f"{LOG_PREFIX} [{manifest.id}] ERROR: {e}")
            import traceback
            traceback.print_exc()
            failed.append(manifest.id)

    if failed:
        print(f"\n{LOG_PREFIX} FAILED strategies: {failed}")
        sys.exit(1)

    print(f"\n{LOG_PREFIX} All strategies complete.")
