"""Tests for backtest engine — regression tests for P1 issues."""

import pandas as pd
import numpy as np
import pytest

from strategies.btc_ma_trend.signal import StrategyConfig, compute_signals
from pipeline.backtest import run_backtest, compute_period_metrics, Trade, _infer_bars_per_year, _annualized_sharpe_from_equity


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
        result = run_backtest(df, config, initial_capital=10000, compute_signals_fn=compute_signals)

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
        result = run_backtest(df, config, initial_capital=10000, compute_signals_fn=compute_signals)

        assert result.has_open_position is True
        # Price went from 110 entry to 100 — unrealized loss
        assert result.total_return_pct < 0

    def test_closed_position_no_open(self):
        """When all positions are closed, has_open_position=False."""
        config = StrategyConfig(ma_window=3, min_hold_bars=1)
        prices = [100, 100, 100, 99, 110, 85]  # buy at 110, sell at 85
        df = make_candles(prices)
        result = run_backtest(df, config, initial_capital=10000, compute_signals_fn=compute_signals)

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
        result = run_backtest(df, config, compute_signals_fn=compute_signals)

        # There should be a buy signal
        buys = [s for s in signals if s.action == "buy"]
        assert len(buys) == 1

        # No completed trades
        assert result.total_trades == 0

        # But result should know there's an open position
        assert result.has_open_position is True

        # And the last signal should be "buy"
        assert signals[-1].action == "buy"


class TestMarkToMarketMetrics:
    """Regression coverage for metrics that must use the full price path."""

    def test_open_position_sharpe_uses_mark_to_market_returns(self):
        """Sharpe should not be forced to 0 just because there are no closed trades."""
        config = StrategyConfig(ma_window=3, min_hold_bars=100)
        prices = [100, 100, 100, 99, 110, 112, 114, 116, 118, 120]
        df = make_candles(prices)

        result = run_backtest(df, config, compute_signals_fn=compute_signals)

        assert result.total_trades == 0
        assert result.has_open_position is True
        assert result.sharpe_ratio > 0

    def test_open_position_drawdown_uses_intraperiod_price_path(self):
        """Open-position drawdown should see peaks and troughs before the final bar."""
        config = StrategyConfig(ma_window=3, min_hold_bars=100)
        prices = [100, 100, 100, 99, 110, 150, 120, 130]
        df = make_candles(prices)

        result = run_backtest(df, config, compute_signals_fn=compute_signals)

        assert result.total_trades == 0
        assert result.has_open_position is True
        assert result.max_drawdown_pct > 15

    def test_buy_hold_max_drawdown_uses_benchmark_price_path(self):
        """B&H max drawdown should be available alongside strategy drawdown."""
        config = StrategyConfig(ma_window=2, min_hold_bars=100)
        prices = [100, 100, 120, 60, 90]
        df = make_candles(prices)

        result = run_backtest(df, config, compute_signals_fn=compute_signals)

        assert result.buy_hold_return_pct == pytest.approx(-25.0)
        assert result.buy_hold_max_drawdown_pct == pytest.approx(50.0)


class TestPeriodMetricsCrossBoundary:
    """P1 regression: compute_period_metrics must include trades that span
    the period boundary (entered before period_start, exited within)."""

    FEE = 0.001

    def _make_trade(self, entry_time, entry_price, exit_time, exit_price, hold_bars):
        entry_cost = entry_price * (1 + self.FEE)
        exit_value = exit_price * (1 - self.FEE)
        pnl_pct = (exit_value - entry_cost) / entry_cost * 100
        pnl_abs = pnl_pct  # simplified, per-unit
        return Trade(
            entry_time=pd.Timestamp(entry_time, tz="UTC"),
            entry_price=entry_price,
            exit_time=pd.Timestamp(exit_time, tz="UTC"),
            exit_price=exit_price,
            hold_bars=hold_bars,
            pnl_pct=pnl_pct,
            pnl_abs=pnl_abs,
        )

    def test_cross_boundary_trade_included(self):
        """A trade entered before period_start and exited after should be
        counted with P&L measured from start_price."""
        trade = self._make_trade("2025-01-01", 50000, "2025-02-01", 60000, 180)
        period_start = pd.Timestamp("2025-01-15", tz="UTC")
        start_price = 55000.0
        end_price = 60000.0

        result = compute_period_metrics(
            [trade], period_start, end_price, start_price,
        )

        assert result is not None
        assert result["total_trades"] == 1
        # Return should be from start_price to exit_price (with exit fee only)
        expected_ret = (60000 * (1 - self.FEE) - 55000) / 55000 * 100
        assert abs(result["total_return_pct"] - round(expected_ret, 2)) < 0.1

    def test_cross_boundary_open_position(self):
        """An open position entered before period_start should measure
        unrealized P&L from start_price, not original entry."""
        period_start = pd.Timestamp("2025-01-15", tz="UTC")
        start_price = 55000.0
        end_price = 65000.0

        result = compute_period_metrics(
            trades=[],
            period_start=period_start,
            end_price=end_price,
            start_price=start_price,
            open_entry_time=pd.Timestamp("2025-01-01", tz="UTC"),
            open_entry_price=50000.0,
        )

        assert result is not None
        assert result["has_open_position"] is True
        assert result["total_trades"] == 0
        # Return from start_price to end_price (exit fee only, no entry fee)
        expected_ret = (65000 * (1 - self.FEE) - 55000) / 55000 * 100
        assert abs(result["total_return_pct"] - round(expected_ret, 2)) < 0.1

    def test_in_period_open_position_uses_entry_price(self):
        """An open position entered within the period should use actual
        entry price with fee, not start_price."""
        period_start = pd.Timestamp("2025-01-15", tz="UTC")
        start_price = 55000.0
        end_price = 65000.0

        result = compute_period_metrics(
            trades=[],
            period_start=period_start,
            end_price=end_price,
            start_price=start_price,
            open_entry_time=pd.Timestamp("2025-02-01", tz="UTC"),
            open_entry_price=58000.0,
        )

        assert result is not None
        # Return from actual entry (with fee) to end_price
        entry_cost = 58000 * (1 + self.FEE)
        expected_ret = (65000 * (1 - self.FEE) - entry_cost) / entry_cost * 100
        assert abs(result["total_return_pct"] - round(expected_ret, 2)) < 0.1

    def test_mixed_cross_and_in_period(self):
        """Both cross-boundary and in-period trades should be counted."""
        cross_trade = self._make_trade("2025-01-01", 50000, "2025-02-01", 60000, 180)
        in_trade = self._make_trade("2025-02-10", 62000, "2025-03-01", 65000, 48)
        period_start = pd.Timestamp("2025-01-15", tz="UTC")

        result = compute_period_metrics(
            [cross_trade, in_trade], period_start,
            end_price=65000.0, start_price=55000.0,
        )

        assert result is not None
        assert result["total_trades"] == 2

    def test_no_activity_returns_none(self):
        """Period with no trades and no open position returns None."""
        trade = self._make_trade("2024-01-01", 50000, "2024-02-01", 55000, 180)
        period_start = pd.Timestamp("2025-01-01", tz="UTC")

        result = compute_period_metrics(
            [trade], period_start, end_price=70000.0, start_price=65000.0,
        )

        assert result is None


class TestSharpeAnnualization:
    """P1 regression: Sharpe must use actual equity cadence, not nominal timeframe."""

    def test_24h_crypto_cadence(self):
        """24/7 crypto with 1H bars → ~8766 bars/year."""
        ts = pd.date_range("2023-01-01", periods=8766 * 2, freq="1h", tz="UTC")
        curve = pd.DataFrame({"timestamp": ts, "equity": np.linspace(100, 120, len(ts))})
        bpy = _infer_bars_per_year(curve)
        assert 8700 < bpy < 8800

    def test_session_filtered_cadence(self):
        """NYSE regular hours (7 bars/day, weekdays) → ~1750-1850 bars/year."""
        timestamps = []
        base = pd.Timestamp("2023-01-02", tz="US/Eastern")
        for day_offset in range(750):
            day = base + pd.Timedelta(days=day_offset)
            if day.weekday() >= 5:
                continue
            for h in [9, 10, 11, 12, 13, 14, 15]:
                timestamps.append(day.replace(hour=h, minute=30).tz_convert("UTC"))

        curve = pd.DataFrame({
            "timestamp": timestamps,
            "equity": np.linspace(100, 200, len(timestamps)),
        })
        bpy = _infer_bars_per_year(curve)
        assert 1750 < bpy < 1900

    def test_4h_crypto_cadence(self):
        """24/7 crypto with 4H bars → ~2190 bars/year."""
        ts = pd.date_range("2023-01-01", periods=2190 * 2, freq="4h", tz="UTC")
        curve = pd.DataFrame({"timestamp": ts, "equity": np.linspace(100, 150, len(ts))})
        bpy = _infer_bars_per_year(curve)
        assert 2150 < bpy < 2250

    def test_short_window_not_collapsed(self):
        """A 22-day session-filtered window must not return raw bar count."""
        timestamps = []
        base = pd.Timestamp("2024-12-02", tz="US/Eastern")
        for day_offset in range(22):
            day = base + pd.Timedelta(days=day_offset)
            if day.weekday() >= 5:
                continue
            for h in [9, 10, 11, 12, 13, 14, 15]:
                timestamps.append(day.replace(hour=h, minute=30).tz_convert("UTC"))

        curve = pd.DataFrame({
            "timestamp": timestamps,
            "equity": np.linspace(100, 105, len(timestamps)),
        })
        bpy = _infer_bars_per_year(curve)
        # Must extrapolate to annual rate (~1800-2200), not return raw count (~105)
        assert bpy > 1500
        assert bpy < 2500

    def test_sharpe_session_filtered_lower_than_naive(self):
        """Session-filtered Sharpe must be lower than naive 8766-factor Sharpe."""
        np.random.seed(42)
        timestamps = []
        base = pd.Timestamp("2023-01-02", tz="US/Eastern")
        for day_offset in range(750):
            day = base + pd.Timedelta(days=day_offset)
            if day.weekday() >= 5:
                continue
            for h in [9, 10, 11, 12, 13, 14, 15]:
                timestamps.append(day.replace(hour=h, minute=30).tz_convert("UTC"))

        n = len(timestamps)
        returns = np.random.normal(0.0005, 0.01, n - 1)
        equity = [100.0]
        for r in returns:
            equity.append(equity[-1] * (1 + r))

        curve = pd.DataFrame({"timestamp": timestamps, "equity": equity})
        sharpe_correct = _annualized_sharpe_from_equity(curve, "1H")

        # Naive: use sqrt(8766) instead of sqrt(~1829)
        rets = pd.Series(equity).pct_change().dropna()
        sharpe_naive = float(rets.mean()) / float(rets.std(ddof=1)) * np.sqrt(8766)

        # Correct Sharpe should be ~sqrt(1829/8766) ≈ 0.46x of naive
        ratio = sharpe_correct / sharpe_naive if sharpe_naive != 0 else 0
        assert 0.35 < ratio < 0.55
