"""Tests for backtest engine — regression tests for P1 issues."""

import pandas as pd
import pytest

from strategies.btc_ma_trend.signal import StrategyConfig, compute_signals
from pipeline.backtest import run_backtest


def make_candles(prices: list[float], start: str = "2024-01-01") -> pd.DataFrame:
    timestamps = pd.date_range(start, periods=len(prices), freq="4h", tz="UTC")
    return pd.DataFrame({"timestamp": timestamps, "close": prices})


class TestUnrealizedPnL:
    """P1 regression: backtest must include unrealized PnL for open positions."""

    def test_open_position_reflected_in_total_return(self):
        """When strategy buys and price rises but never sells,
        total_return should reflect the unrealized gain, not be 0."""
        config = StrategyConfig(ma_window=3, min_hold_bars=100)
        # Cross above MA then price keeps rising — never reaches sell condition
        prices = [100, 100, 100, 99, 110, 120, 130, 140, 150]
        df = make_candles(prices)
        result = run_backtest(df, config, initial_capital=10000)

        # No completed trades, but should have open position
        assert result.total_trades == 0
        assert result.has_open_position is True
        # Total return should be positive (mark-to-market)
        assert result.total_return_pct > 0
        # Realized return should be 0 (no closed trades)
        assert result.realized_return_pct == 0.0

    def test_open_position_with_loss(self):
        """Unrealized loss should also be reflected."""
        config = StrategyConfig(ma_window=3, min_hold_bars=100)
        prices = [100, 100, 100, 99, 110, 105, 102, 101, 100]
        df = make_candles(prices)
        result = run_backtest(df, config, initial_capital=10000)

        assert result.has_open_position is True
        # Price went from 110 entry to 100 — unrealized loss
        assert result.total_return_pct < 0

    def test_closed_position_no_open(self):
        """When all positions are closed, has_open_position=False."""
        config = StrategyConfig(ma_window=3, min_hold_bars=1)
        prices = [100, 100, 100, 99, 110, 85]  # buy at 110, sell at 85
        df = make_candles(prices)
        result = run_backtest(df, config, initial_capital=10000)

        assert result.has_open_position is False
        assert result.total_trades == 1
        # total_return and realized_return should match
        assert abs(result.total_return_pct - result.realized_return_pct) < 0.01


class TestPositionDetection:
    """P1 regression: position state must use signal sequence, not trades list."""

    def test_first_buy_no_trades_still_in_position(self):
        """If first buy happens but never sells (0 completed trades),
        signal sequence should still show we're in position."""
        config = StrategyConfig(ma_window=3, min_hold_bars=100)
        prices = [100, 100, 100, 99, 110, 115, 120]
        df = make_candles(prices)

        signals = compute_signals(df, config)
        result = run_backtest(df, config)

        # There should be a buy signal
        buys = [s for s in signals if s.action == "buy"]
        assert len(buys) == 1

        # No completed trades
        assert result.total_trades == 0

        # But result should know there's an open position
        assert result.has_open_position is True

        # And the last signal should be "buy"
        assert signals[-1].action == "buy"
