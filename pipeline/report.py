from __future__ import annotations

"""Report generator - produces JSON status + PNG charts.

Generates strategy status files and backtest visualization charts
that can be served as static content on the website.

Strategy functions are injected by the caller, never imported directly.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

from pipeline.backtest import BacktestResult, result_to_dict


def _hours_per_bar(timeframe: str) -> float:
    """Parse timeframe string to hours per bar."""
    tf = timeframe.strip().upper()
    if tf.endswith("H"):
        return float(tf[:-1])
    if tf.endswith("D"):
        return float(tf[:-1]) * 24
    return 4.0  # default


def generate_status_json(
    df: pd.DataFrame,
    config,
    in_position: bool,
    entry_bar_idx: int,
    output_path: Path,
    current_signal=None,
) -> dict:
    """Generate current strategy status as JSON.

    Args:
        df: Recent candle data sorted oldest-first.
        config: Strategy configuration (duck-typed).
        in_position: Current position state.
        entry_bar_idx: Bar index of entry.
        output_path: Path to write JSON file.
        current_signal: Pre-computed current signal (injected by caller).

    Returns:
        Status dict.
    """
    if current_signal is None:
        raise ValueError("current_signal must be provided (injected by caller)")

    signal = current_signal

    signal_dict = {
        "action": signal.action,
        "price": round(signal.price, 2),
        "ma_value": round(signal.ma_value, 2),
        "timestamp": signal.timestamp.isoformat(),
        "hold_bars": signal.hold_bars,
        "reason": signal.reason,
    }

    # EchoTrend-specific structured fields
    if hasattr(signal, 'regime') and signal.regime is not None:
        signal_dict["regime"] = signal.regime
    if hasattr(signal, 'mode') and signal.mode is not None:
        signal_dict["mode"] = signal.mode
    if hasattr(signal, 'target_exposure') and signal.target_exposure is not None:
        signal_dict["target_exposure"] = round(signal.target_exposure, 4)
    if hasattr(signal, 'scores') and signal.scores:
        signal_dict["scores"] = {k: round(v, 2) if isinstance(v, float) else v for k, v in signal.scores.items()}

    status = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "strategy": {
            "name": config.display_name,
            "code": config.internal_code,
            "timeframe": config.timeframe,
            "symbol": config.symbol,
        },
        "current_signal": signal_dict,
        "position": {
            "in_position": in_position,
            "entry_bar_idx": entry_bar_idx if in_position else None,
        },
    }

    # Strategy-specific fields (optional, only added if config provides them)
    ma_window = getattr(config, 'ma_window', None)
    if ma_window is not None:
        status["strategy"]["ma_window"] = ma_window
    min_hold_bars = getattr(config, 'min_hold_bars', None)
    if min_hold_bars is not None:
        status["strategy"]["min_hold_bars"] = min_hold_bars
        status["strategy"]["min_hold_days"] = min_hold_bars * _hours_per_bar(config.timeframe) / 24
    exit_confirm_bars = getattr(config, 'exit_confirm_bars', None)
    if exit_confirm_bars is not None:
        status["strategy"]["exit_confirm_bars"] = exit_confirm_bars

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(status, f, indent=2, default=str)

    return status


def generate_backtest_json(
    result: BacktestResult,
    output_path: Path,
    since_date: str | None = None,
    periods: dict | None = None,
) -> None:
    """Write backtest results to JSON.

    Args:
        result: Backtest result.
        output_path: Path to write JSON file.
        since_date: Fixed launch reference date (ISO string).
        periods: Period-filtered performance data.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = result_to_dict(result)
    if since_date:
        data["since_date"] = since_date
    if periods:
        data["periods"] = periods
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def generate_equity_chart(
    result: BacktestResult,
    output_path: Path,
    benchmark_prices: pd.DataFrame | None = None,
) -> None:
    """Generate equity curve chart with optional B&H benchmark as PNG.

    Args:
        result: Backtest result with equity_curve.
        output_path: Path to write PNG file.
        benchmark_prices: DataFrame with ['timestamp', 'close'] for B&H overlay.
            Normalized to match strategy's starting equity.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(12, 5))
    eq = result.equity_curve

    # B&H benchmark: normalize price series to same starting equity
    if benchmark_prices is not None and not benchmark_prices.empty:
        bm = benchmark_prices.copy().sort_values("timestamp").reset_index(drop=True)
        # Align to equity curve time range
        eq_start = eq["timestamp"].iloc[0]
        bm = bm[bm["timestamp"] >= eq_start].reset_index(drop=True)
        if len(bm) > 1:
            start_equity = float(eq["equity"].iloc[0])
            start_price = float(bm["close"].iloc[0])
            bm["equity"] = bm["close"].astype(float) / start_price * start_equity
            ax.plot(bm["timestamp"], bm["equity"], color="#9ca3af", linewidth=1.0,
                    linestyle="--", label="Buy & Hold", alpha=0.8)

    ax.plot(eq["timestamp"], eq["equity"], color="#2563eb", linewidth=1.5, label="Strategy")
    ax.fill_between(eq["timestamp"], eq["equity"], alpha=0.1, color="#2563eb")

    ax.set_title(
        f"Equity Curve - {result.config.display_name}",
        fontsize=14, fontweight="bold",
    )
    ax.set_ylabel("Equity (USDT)")
    ax.legend(loc="upper left")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def generate_price_ma_chart(
    df: pd.DataFrame,
    config,
    trades: list,
    output_path: Path,
    last_n_bars: int = 500,
) -> None:
    """Generate price chart with optional MA overlay and trade markers.

    Args:
        config: Strategy configuration (duck-typed).
            Required: timeframe, display_name, symbol.
            Optional: ma_window (if absent, MA overlay is skipped).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    df = df.copy().sort_values("timestamp").reset_index(drop=True)

    ma_window = getattr(config, 'ma_window', None)
    if ma_window is not None:
        df["ma"] = df["close"].rolling(window=ma_window, min_periods=ma_window).mean()

    if len(df) > last_n_bars:
        df = df.iloc[-last_n_bars:]

    symbol = getattr(config, 'symbol', 'BTC-USDT')
    base_asset = symbol.split('-')[0] if '-' in symbol else symbol

    fig, ax = plt.subplots(figsize=(14, 6))
    ax.plot(df["timestamp"], df["close"], color="#374151", linewidth=0.8, label=f"{base_asset} Close")
    if ma_window is not None and "ma" in df.columns:
        ax.plot(df["timestamp"], df["ma"], color="#f59e0b", linewidth=1.2, label=f"MA{ma_window}")

    # Mark trades
    for t in trades:
        entry_time = t.entry_time if isinstance(t.entry_time, pd.Timestamp) else pd.Timestamp(t.entry_time)
        exit_time = t.exit_time if isinstance(t.exit_time, pd.Timestamp) else pd.Timestamp(t.exit_time)

        if entry_time >= df.iloc[0]["timestamp"]:
            ax.scatter(entry_time, t.entry_price, color="#10b981", marker="^", s=80, zorder=5)
        if exit_time >= df.iloc[0]["timestamp"]:
            color = "#ef4444" if t.pnl_pct < 0 else "#10b981"
            ax.scatter(exit_time, t.exit_price, color=color, marker="v", s=80, zorder=5)

    ax.set_title(
        f"{symbol.replace('-', '/')} {config.timeframe} - {config.display_name}",
        fontsize=14, fontweight="bold",
    )
    ax.set_ylabel("Price (USDT)")
    ax.legend(loc="upper left")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
