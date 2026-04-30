"""Backtesting engine for MA trend strategy.

Simulates trading over historical data and computes performance metrics.
"""

from dataclasses import dataclass, field

import pandas as pd

from strategies.btc_ma_trend.signal import StrategyConfig, compute_signals


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


@dataclass
class BacktestResult:
    """Results from a backtest run."""

    config: StrategyConfig
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
    has_open_position: bool


def run_backtest(
    df: pd.DataFrame,
    config: StrategyConfig | None = None,
    initial_capital: float = 10000.0,
    fee_rate: float = 0.001,
) -> BacktestResult:
    """Run a backtest on historical data.

    Args:
        df: DataFrame with ['timestamp', 'close'], sorted oldest-first.
        config: Strategy configuration.
        initial_capital: Starting capital in quote currency (USDT).
        fee_rate: Trading fee rate per side (0.001 = 0.1%).

    Returns:
        BacktestResult with trades, equity curve, and performance metrics.
    """
    if config is None:
        config = StrategyConfig()

    signals = compute_signals(df, config)

    trades: list[Trade] = []
    equity = initial_capital
    peak_equity = initial_capital
    max_drawdown = 0.0

    equity_points: list[dict] = [{"timestamp": df.iloc[0]["timestamp"], "equity": equity}]

    entry_price = 0.0
    entry_time = pd.Timestamp("1970-01-01")

    for sig in signals:
        if sig.action == "buy":
            entry_price = sig.price * (1 + fee_rate)  # slippage via fee
            entry_time = sig.timestamp
        elif sig.action == "sell" and entry_price > 0:
            exit_price = sig.price * (1 - fee_rate)
            pnl_pct = (exit_price - entry_price) / entry_price
            pnl_abs = equity * pnl_pct
            equity += pnl_abs

            trades.append(Trade(
                entry_time=entry_time,
                entry_price=entry_price / (1 + fee_rate),  # record pre-fee price
                exit_time=sig.timestamp,
                exit_price=sig.price,
                hold_bars=sig.hold_bars,
                pnl_pct=pnl_pct * 100,
                pnl_abs=pnl_abs,
            ))

            equity_points.append({"timestamp": sig.timestamp, "equity": equity})

            if equity > peak_equity:
                peak_equity = equity
            dd = (peak_equity - equity) / peak_equity
            if dd > max_drawdown:
                max_drawdown = dd

            entry_price = 0.0

    # Mark-to-market: account for unrealized PnL if position is open
    realized_equity = equity
    has_open_position = bool(entry_price > 0)
    if has_open_position:
        last_close = df.iloc[-1]["close"]
        unrealized_pnl_pct = (last_close * (1 - fee_rate) - entry_price) / entry_price
        equity_mtm = equity + equity * unrealized_pnl_pct
        equity_points.append({"timestamp": df.iloc[-1]["timestamp"], "equity": equity_mtm})
        if equity_mtm > peak_equity:
            peak_equity = equity_mtm
        dd = (peak_equity - equity_mtm) / peak_equity
        if dd > max_drawdown:
            max_drawdown = dd
    else:
        equity_mtm = equity

    # Compute metrics
    total_trades = len(trades)
    wins = sum(1 for t in trades if t.pnl_pct > 0)
    win_rate = wins / total_trades if total_trades > 0 else 0.0
    avg_hold = sum(t.hold_bars for t in trades) / total_trades if total_trades > 0 else 0.0
    realized_return = (realized_equity - initial_capital) / initial_capital * 100
    total_return = (equity_mtm - initial_capital) / initial_capital * 100

    # Buy & hold benchmark
    first_price = df.iloc[config.ma_window]["close"] if len(df) > config.ma_window else df.iloc[0]["close"]
    last_price = df.iloc[-1]["close"]
    buy_hold_return = (last_price - first_price) / first_price * 100

    # Sharpe ratio (simplified: using trade returns)
    if total_trades > 1:
        returns = [t.pnl_pct for t in trades]
        avg_ret = sum(returns) / len(returns)
        std_ret = (sum((r - avg_ret) ** 2 for r in returns) / (len(returns) - 1)) ** 0.5
        sharpe = avg_ret / std_ret if std_ret > 0 else 0.0
    else:
        sharpe = 0.0

    equity_df = pd.DataFrame(equity_points)

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

    Returns:
        Dict with period metrics, or None if no activity in period.
    """
    # Trades fully within the period
    in_period = [t for t in trades if t.entry_time >= period_start]

    # Trades crossing the boundary (entered before period, exited within)
    cross_boundary = [
        t for t in trades
        if t.entry_time < period_start and t.exit_time >= period_start
    ]

    # Open position contributes regardless of when it was entered
    include_open = open_entry_time is not None
    open_crosses_boundary = include_open and open_entry_time < period_start

    if not in_period and not cross_boundary and not include_open:
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
            unrealized_ret = (end_price * (1 - fee_rate) - start_price) / start_price
        else:
            # Entered within period — use actual entry with fee
            entry_cost = open_entry_price * (1 + fee_rate)
            exit_value = end_price * (1 - fee_rate)
            unrealized_ret = (exit_value - entry_cost) / entry_cost
        equity *= (1 + unrealized_ret)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak
        if dd > max_dd:
            max_dd = dd

    closed_count = len(in_period) + len(cross_boundary)

    # Sharpe ratio from closed trade returns
    if len(returns_list) > 1:
        avg_ret = sum(returns_list) / len(returns_list)
        std_ret = (sum((r - avg_ret) ** 2 for r in returns_list) / (len(returns_list) - 1)) ** 0.5
        sharpe = avg_ret / std_ret if std_ret > 0 else 0.0
    else:
        sharpe = 0.0

    buy_hold = (end_price - start_price) / start_price * 100 if start_price > 0 else 0.0
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
        "has_open_position": include_open,
    }


def result_to_dict(result: BacktestResult) -> dict:
    """Convert BacktestResult to a JSON-serializable dict."""
    return {
        "strategy": {
            "ma_window": result.config.ma_window,
            "min_hold_bars": result.config.min_hold_bars,
            "timeframe": result.config.timeframe,
            "symbol": result.config.symbol,
        },
        "performance": {
            "total_return_pct": round(result.total_return_pct, 2),
            "realized_return_pct": round(result.realized_return_pct, 2),
            "max_drawdown_pct": round(result.max_drawdown_pct, 2),
            "win_rate_pct": round(result.win_rate, 2),
            "total_trades": result.total_trades,
            "avg_hold_bars": round(result.avg_hold_bars, 1),
            "sharpe_ratio": round(result.sharpe_ratio, 3),
            "buy_hold_return_pct": round(result.buy_hold_return_pct, 2),
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
            }
            for t in result.trades
        ],
    }
