from __future__ import annotations

"""Backtesting engine - strategy-agnostic.

Simulates trading over historical data and computes performance metrics.
Strategy functions are injected by the caller (core/runner.py), never imported directly.
"""

from dataclasses import dataclass, asdict, fields
from math import sqrt

import pandas as pd


@dataclass
class Trade:
    """A completed round-trip trade."""

    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    hold_bars: int
    pnl_pct: float  # percentage return
    pnl_abs: float  # absolute return per unit
    asset: str = ""
    status: str = "closed"


@dataclass
class BacktestResult:
    """Results from a backtest run."""

    config: any  # Strategy-specific config (injected, not imported)
    trades: list[Trade]
    equity_curve: pd.DataFrame  # timestamp, equity
    total_return_pct: float  # mark-to-market (includes unrealized)
    realized_return_pct: float  # closed trades only
    max_drawdown_pct: float
    win_rate: float
    total_trades: int
    avg_hold_bars: float
    sharpe_ratio: float
    start_date: pd.Timestamp
    end_date: pd.Timestamp
    buy_hold_return_pct: float
    buy_hold_max_drawdown_pct: float
    has_open_position: bool


def _bars_per_year(timeframe: str) -> float:
    """Return annualization factor for a candle timeframe."""
    value = timeframe.strip().upper()
    if value.endswith("H"):
        return 365 * 24 / float(value[:-1])
    if value.endswith("D"):
        return 365 / float(value[:-1])
    return 365.0


def _max_drawdown(values) -> float:
    """Compute max drawdown from a price or equity path."""
    series = [float(v) for v in values if pd.notna(v)]
    if not series:
        return 0.0

    peak = series[0]
    max_dd = 0.0
    for value in series:
        if value > peak:
            peak = value
        if peak <= 0:
            continue
        dd = (peak - value) / peak
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _infer_bars_per_year(equity_curve: pd.DataFrame) -> float:
    """Derive annualization factor from actual equity curve bar density.

    Uses total bars / elapsed years. Works correctly for both full-history
    and short-window (period) equity curves, and for session-filtered data.
    """
    ts = pd.to_datetime(equity_curve["timestamp"])
    if len(ts) < 2:
        return 365.0
    span_seconds = (ts.iloc[-1] - ts.iloc[0]).total_seconds()
    if span_seconds <= 0:
        return 365.0
    elapsed_years = span_seconds / (365.25 * 24 * 3600)
    return (len(ts) - 1) / elapsed_years


def _annualized_sharpe_from_equity(equity_curve: pd.DataFrame, timeframe: str) -> float:
    """Compute annualized Sharpe from mark-to-market equity returns.

    Annualization factor is derived from the actual equity curve timestamp
    cadence rather than the nominal timeframe, so session-filtered strategies
    (e.g. NYSE regular hours only) get the correct factor automatically.
    """
    if len(equity_curve) < 3:
        return 0.0

    returns = equity_curve["equity"].astype(float).pct_change().dropna()
    if len(returns) < 2:
        return 0.0

    avg_ret = float(returns.mean())
    std_ret = float(returns.std(ddof=1))
    if std_ret <= 0:
        return 0.0

    bars_yr = _infer_bars_per_year(equity_curve)
    return avg_ret / std_ret * sqrt(bars_yr)


def _period_equity_curve(equity_curve: pd.DataFrame, period_start: pd.Timestamp) -> pd.DataFrame:
    """Slice an equity curve with the last point before period_start as baseline."""
    if equity_curve.empty:
        return equity_curve

    curve = equity_curve.sort_values("timestamp").reset_index(drop=True)
    before_or_at = curve[curve["timestamp"] <= period_start]
    after = curve[curve["timestamp"] > period_start]

    if before_or_at.empty:
        return curve[curve["timestamp"] >= period_start].reset_index(drop=True)

    baseline = before_or_at.tail(1)
    return pd.concat([baseline, after], ignore_index=True)


def run_backtest(
    df: pd.DataFrame,
    config=None,
    initial_capital: float = 10000.0,
    fee_rate: float = 0.001,
    signals: list | None = None,
    compute_signals_fn=None,
) -> BacktestResult:
    """Run a backtest on historical data.

    Args:
        df: DataFrame with ['timestamp', 'close'], sorted oldest-first.
        config: Strategy configuration (duck-typed).
        initial_capital: Starting capital in quote currency (USDT).
        fee_rate: Trading fee rate per side (0.001 = 0.1%).
        signals: Pre-computed signals. If provided, used directly.
        compute_signals_fn: Signal computation function. Used if signals not provided.

    Returns:
        BacktestResult with trades, equity curve, and performance metrics.

    Raises:
        ValueError: If neither signals nor compute_signals_fn is provided.
    """
    if config is None:
        raise ValueError("config is required")

    df = df.copy().sort_values("timestamp").reset_index(drop=True)

    if signals is not None:
        pass  # use pre-computed signals
    elif compute_signals_fn is not None:
        signals = compute_signals_fn(df, config)
    else:
        raise ValueError("Either signals or compute_signals_fn must be provided")

    signals_by_timestamp = {sig.timestamp: sig for sig in signals}

    trades: list[Trade] = []
    cash = initial_capital
    realized_equity = initial_capital
    position_units = 0.0
    entry_price_with_fee = 0.0
    entry_equity = 0.0
    entry_time = pd.Timestamp("1970-01-01")
    equity_points: list[dict] = []

    for _, row in df.iterrows():
        timestamp = row["timestamp"]
        close = float(row["close"])
        sig = signals_by_timestamp.get(timestamp)

        if sig and sig.action == "buy" and position_units == 0:
            entry_price_with_fee = sig.price * (1 + fee_rate)
            entry_equity = cash
            position_units = cash / entry_price_with_fee
            cash = 0.0
            entry_time = sig.timestamp
        elif sig and sig.action == "sell" and position_units > 0:
            exit_value = position_units * sig.price * (1 - fee_rate)
            pnl_pct = (exit_value - entry_equity) / entry_equity
            pnl_abs = exit_value - entry_equity
            cash = exit_value
            realized_equity = cash

            trades.append(Trade(
                entry_time=entry_time,
                entry_price=entry_price_with_fee / (1 + fee_rate),  # record pre-fee price
                exit_time=sig.timestamp,
                exit_price=sig.price,
                hold_bars=sig.hold_bars,
                pnl_pct=pnl_pct * 100,
                pnl_abs=pnl_abs,
            ))

            position_units = 0.0
            entry_price_with_fee = 0.0
            entry_equity = 0.0

        current_equity = cash if position_units == 0 else position_units * close * (1 - fee_rate)
        equity_points.append({"timestamp": timestamp, "equity": current_equity})

    equity_df = pd.DataFrame(equity_points)
    equity_mtm = float(equity_df.iloc[-1]["equity"])
    max_drawdown = _max_drawdown(equity_df["equity"])
    sharpe = _annualized_sharpe_from_equity(equity_df, config.timeframe)
    has_open_position = bool(position_units > 0)

    total_trades = len(trades)
    wins = sum(1 for t in trades if t.pnl_pct > 0)
    win_rate = wins / total_trades if total_trades > 0 else 0.0
    avg_hold = sum(t.hold_bars for t in trades) / total_trades if total_trades > 0 else 0.0
    realized_return = (realized_equity - initial_capital) / initial_capital * 100
    total_return = (equity_mtm - initial_capital) / initial_capital * 100

    warmup = getattr(config, 'warmup_bars', getattr(config, 'ma_window', 0))
    first_price_idx = warmup if len(df) > warmup else 0
    buy_hold_prices = df.iloc[first_price_idx:]["close"].astype(float)
    first_price = float(buy_hold_prices.iloc[0])
    last_price = float(buy_hold_prices.iloc[-1])
    buy_hold_return = (last_price - first_price) / first_price * 100
    buy_hold_max_drawdown = _max_drawdown(buy_hold_prices) * 100

    return BacktestResult(
        config=config,
        trades=trades,
        equity_curve=equity_df,
        total_return_pct=total_return,
        realized_return_pct=realized_return,
        max_drawdown_pct=max_drawdown * 100,
        win_rate=win_rate * 100,
        total_trades=total_trades,
        avg_hold_bars=avg_hold,
        sharpe_ratio=sharpe,
        start_date=df.iloc[0]["timestamp"],
        end_date=df.iloc[-1]["timestamp"],
        buy_hold_return_pct=buy_hold_return,
        buy_hold_max_drawdown_pct=buy_hold_max_drawdown,
        has_open_position=has_open_position,
    )


def compute_period_metrics(
    trades: list[Trade],
    period_start: pd.Timestamp,
    end_price: float,
    start_price: float,
    open_entry_time: pd.Timestamp | None = None,
    open_entry_price: float = 0.0,
    fee_rate: float = 0.001,
    equity_curve: pd.DataFrame | None = None,
    benchmark_prices=None,
    use_equity_curve_returns: bool = False,
    timeframe: str = "4H",
) -> dict | None:
    """Compute performance metrics for a period, including cross-boundary trades.

    Trades that were entered before period_start but exited within the period
    are included with P&L measured from start_price (not original entry).

    Args:
        trades: All completed trades from backtest.
        period_start: Start of the period (inclusive, tz-aware UTC).
        end_price: Last close price (for buy_hold and unrealized PnL).
        start_price: Close price at period_start (for buy_hold).
        open_entry_time: Entry time of open position (if any).
        open_entry_price: Entry price of open position (pre-fee).
        fee_rate: Fee rate per side.
        equity_curve: Full mark-to-market equity curve for time-based Sharpe/drawdown.
        benchmark_prices: B&H price path for the same period.
        timeframe: Candle timeframe used for Sharpe annualization.

    Returns:
        Dict with period metrics, or None if no activity in period.
    """
    closed_trades = [
        t for t in trades
        if getattr(t, "status", "closed") != "open"
    ]
    open_trades = [
        t for t in trades
        if getattr(t, "status", "closed") == "open" and t.exit_time >= period_start
    ]

    # Trades fully within the period
    in_period = [t for t in closed_trades if t.entry_time >= period_start]

    # Trades crossing the boundary (entered before period, exited within)
    cross_boundary = [
        t for t in closed_trades
        if t.entry_time < period_start and t.exit_time >= period_start
    ]

    # Open position contributes regardless of when it was entered
    open_trade = open_trades[-1] if open_trades else None
    open_exit_price = end_price
    if open_entry_time is None and open_trade is not None:
        open_entry_time = open_trade.entry_time
        open_entry_price = open_trade.entry_price
        open_exit_price = open_trade.exit_price
    include_open = open_entry_time is not None
    open_crosses_boundary = include_open and open_entry_time < period_start

    has_equity_data = equity_curve is not None and not equity_curve.empty

    if not in_period and not cross_boundary and not include_open and not has_equity_data:
        return None

    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    wins = 0
    returns_list: list[float] = []

    # Cross-boundary trades: P&L from start_price to exit (no entry fee)
    for t in cross_boundary:
        exit_value = t.exit_price * (1 - fee_rate)
        ret = (exit_value - start_price) / start_price
        equity *= (1 + ret)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak
        if dd > max_dd:
            max_dd = dd
        if ret > 0:
            wins += 1
        returns_list.append(ret * 100)

    # In-period trades: use original P&L
    for t in in_period:
        ret = t.pnl_pct / 100
        equity *= (1 + ret)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak
        if dd > max_dd:
            max_dd = dd
        if t.pnl_pct > 0:
            wins += 1
        returns_list.append(t.pnl_pct)

    realized_equity = equity

    # Unrealized P&L from open position
    if include_open:
        if open_crosses_boundary:
            # Entered before period — measure from start_price (no entry fee)
            unrealized_ret = (open_exit_price * (1 - fee_rate) - start_price) / start_price
        else:
            # Entered within period — use actual entry with fee
            entry_cost = open_entry_price * (1 + fee_rate)
            exit_value = open_exit_price * (1 - fee_rate)
            unrealized_ret = (exit_value - entry_cost) / entry_cost
        equity *= (1 + unrealized_ret)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak
        if dd > max_dd:
            max_dd = dd

    closed_count = len(in_period) + len(cross_boundary)

    # Sharpe ratio from closed trade returns, unless full MTM equity is available.
    if len(returns_list) > 1:
        avg_ret = sum(returns_list) / len(returns_list)
        std_ret = (sum((r - avg_ret) ** 2 for r in returns_list) / (len(returns_list) - 1)) ** 0.5
        sharpe = avg_ret / std_ret if std_ret > 0 else 0.0
    else:
        sharpe = 0.0

    if equity_curve is not None:
        period_curve = _period_equity_curve(equity_curve, period_start)
        if not period_curve.empty and len(period_curve) >= 2:
            max_dd = _max_drawdown(period_curve["equity"])
            sharpe = _annualized_sharpe_from_equity(period_curve, timeframe)
            if use_equity_curve_returns:
                eq_start = float(period_curve.iloc[0]["equity"])
                eq_end = float(period_curve.iloc[-1]["equity"])
                if eq_start > 0:
                    equity = eq_end / eq_start
                    realized_equity = equity

    buy_hold = (end_price - start_price) / start_price * 100 if start_price > 0 else 0.0
    buy_hold_max_dd = _max_drawdown(benchmark_prices) * 100 if benchmark_prices is not None else 0.0
    all_closed = cross_boundary + in_period
    avg_hold = sum(t.hold_bars for t in all_closed) / closed_count if closed_count > 0 else 0.0

    return {
        "total_return_pct": round((equity - 1) * 100, 2),
        "realized_return_pct": round((realized_equity - 1) * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "win_rate_pct": round(wins / closed_count * 100, 2) if closed_count > 0 else 0.0,
        "total_trades": closed_count,
        "avg_hold_bars": round(avg_hold, 1),
        "sharpe_ratio": round(sharpe, 3),
        "buy_hold_return_pct": round(buy_hold, 2),
        "buy_hold_max_drawdown_pct": round(buy_hold_max_dd, 2),
        "has_open_position": include_open,
    }


def _config_to_dict(config) -> dict:
    """Serialize strategy config to dict (works with any dataclass config)."""
    import dataclasses
    if dataclasses.is_dataclass(config) and not isinstance(config, type):
        return dataclasses.asdict(config)
    return {k: v for k, v in vars(config).items() if not k.startswith('_')}


def result_to_dict(result: BacktestResult) -> dict:
    """Convert BacktestResult to a JSON-serializable dict."""
    return {
        "strategy": _config_to_dict(result.config),
        "performance": {
            "total_return_pct": round(result.total_return_pct, 2),
            "realized_return_pct": round(result.realized_return_pct, 2),
            "max_drawdown_pct": round(result.max_drawdown_pct, 2),
            "win_rate_pct": round(result.win_rate, 2),
            "total_trades": result.total_trades,
            "avg_hold_bars": round(result.avg_hold_bars, 1),
            "sharpe_ratio": round(result.sharpe_ratio, 3),
            "buy_hold_return_pct": round(result.buy_hold_return_pct, 2),
            "buy_hold_max_drawdown_pct": round(result.buy_hold_max_drawdown_pct, 2),
            "has_open_position": result.has_open_position,
        },
        "period": {
            "start": result.start_date.isoformat(),
            "end": result.end_date.isoformat(),
        },
        "trades": [
            {
                "entry_time": t.entry_time.isoformat(),
                "entry_price": round(t.entry_price, 2),
                "exit_time": t.exit_time.isoformat(),
                "exit_price": round(t.exit_price, 2),
                "hold_bars": t.hold_bars,
                "pnl_pct": round(t.pnl_pct, 2),
                **({"asset": t.asset} if getattr(t, "asset", "") else {}),
                **({"status": t.status} if getattr(t, "status", "closed") != "closed" else {}),
            }
            for t in result.trades
        ],
    }
