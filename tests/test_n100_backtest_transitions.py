"""Regression tests for N100 Guard-Z backtest transition day return attribution.

Verifies:
- ENTRY day: earn money_market (was in cash overnight), not new ETF return
- EXIT day: earn old ETF return (held overnight), not money_market
- ROTATION day: earn old ETF return (held overnight), not new ETF return
- Trade PnL uses actual ETF adj_open prices
- Boundary: risk_on=True but selected_etf="" → treated as cash
"""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from strategies.n100_guard_z.signal import StrategyConfig, run_backtest


def _make_etf_prices(dates, code, base=2.0, daily_pct=0.01):
    """Create ETF DataFrame with close/open/adj_close columns (long format per ETF)."""
    n = len(dates)
    closes = [base * (1 + daily_pct) ** i for i in range(n)]
    opens = [c * 0.999 for c in closes]  # open slightly below close
    adj_closes = closes[:]  # no splits
    df = pd.DataFrame({
        "timestamp": dates,
        "close": closes,
        "open": opens,
        "adj_close": adj_closes,
    })
    return df


def _make_controlled_backtest_data(n_days=30):
    """Build synthetic data with controlled signal transitions.

    Signal pattern (risk_on):
      days 0-9: False (cash)
      days 10-19: True, etf=513100 (invested)
      days 20-24: False (cash again)
      days 25-29: True, etf=513100 (re-entry)

    This gives us:
      day 10: ENTRY (cash→invested)
      day 20: EXIT (invested→cash)
      day 25: ENTRY (cash→invested again)
    """
    dates = pd.date_range("2024-01-01", periods=n_days, freq="D", tz="UTC")

    # QQQ/SPY (only needed for function signature, signals are mocked)
    qqq = pd.DataFrame({"timestamp": dates, "close": np.linspace(100, 130, n_days)})
    spy = pd.DataFrame({"timestamp": dates, "close": np.linspace(100, 115, n_days)})

    # ETF with known constant 1% daily return
    etf_513100 = _make_etf_prices(dates, "513100", base=2.0, daily_pct=0.01)
    nav_df = pd.DataFrame({"timestamp": dates, "513100": etf_513100["close"] * 0.998})

    # Controlled signal layer
    risk_on = [False] * 10 + [True] * 10 + [False] * 5 + [True] * 5
    signal_layer = pd.DataFrame({
        "timestamp": dates,
        "qqq_close": qqq["close"],
        "risk_on": risk_on,
        "state": ["TRUE_CASH_STRETCH" if not r else "NDX_INVESTED" for r in risk_on],
    })

    # Controlled rotation (always selects 513100 when risk_on)
    rotation = pd.DataFrame({
        "selected_etf": ["513100" if r else "" for r in risk_on],
    })

    extra_data = {
        "spy": spy,
        "etf_513100": etf_513100,
        "etf_nav": nav_df,
    }

    return qqq, extra_data, signal_layer, rotation


def _make_rotation_data(n_days=30):
    """Build data with a ROTATION transition.

    Signal pattern:
      days 0-4: False (cash)
      days 5-14: True, etf=513100
      days 15-24: True, etf=159941 (ROTATION on day 15)
      days 25-29: False (EXIT on day 25)
    """
    dates = pd.date_range("2024-01-01", periods=n_days, freq="D", tz="UTC")

    qqq = pd.DataFrame({"timestamp": dates, "close": np.linspace(100, 130, n_days)})
    spy = pd.DataFrame({"timestamp": dates, "close": np.linspace(100, 115, n_days)})

    # Two ETFs with different returns
    etf_513100 = _make_etf_prices(dates, "513100", base=2.0, daily_pct=0.01)
    etf_159941 = _make_etf_prices(dates, "159941", base=1.5, daily_pct=0.015)

    nav_df = pd.DataFrame({
        "timestamp": dates,
        "513100": etf_513100["close"] * 0.998,
        "159941": etf_159941["close"] * 0.998,
    })

    risk_on = [False] * 5 + [True] * 20 + [False] * 5
    selected = [""] * 5 + ["513100"] * 10 + ["159941"] * 10 + [""] * 5

    signal_layer = pd.DataFrame({
        "timestamp": dates,
        "qqq_close": qqq["close"],
        "risk_on": risk_on,
        "state": ["TRUE_CASH_STRETCH" if not r else "NDX_INVESTED" for r in risk_on],
    })
    rotation = pd.DataFrame({"selected_etf": selected})

    extra_data = {
        "spy": spy,
        "etf_513100": etf_513100,
        "etf_159941": etf_159941,
        "etf_nav": nav_df,
    }

    return qqq, extra_data, signal_layer, rotation


class TestTransitionDayAttribution:
    """Test that transition days attribute returns to the correct overnight state."""

    @patch("strategies.n100_guard_z.signal._fastre_signals")
    @patch("strategies.n100_guard_z.signal._zscore_rotation")
    def test_entry_day_earns_money_market(self, mock_rotation, mock_fastre):
        """ENTRY day: was in cash overnight → should earn money_market, not ETF return."""
        qqq, extra_data, signal_layer, rotation = _make_controlled_backtest_data()

        mock_fastre.return_value = signal_layer
        mock_rotation.return_value = rotation

        config = StrategyConfig(fee_rate=0.001, money_market_annual_rate=0.02)
        result = run_backtest(qqq, config, extra_data=extra_data)

        eq = result.equity_curve["equity"].tolist()
        daily_mm = config.money_market_annual_rate / 365

        # With shift(1) on selected_etf: risk_on goes True at day_10 but
        # shifted_etf doesn't become "513100" until day_11 (rotation[10]).
        # So effective ENTRY is at i=11 → equity[11].
        entry_return = eq[11] / eq[10] - 1
        expected_entry = daily_mm - config.fee_rate
        assert abs(entry_return - expected_entry) < 1e-8, (
            f"ENTRY day return {entry_return:.6f} != expected {expected_entry:.6f}"
        )

    @patch("strategies.n100_guard_z.signal._fastre_signals")
    @patch("strategies.n100_guard_z.signal._zscore_rotation")
    def test_exit_day_earns_etf_return(self, mock_rotation, mock_fastre):
        """EXIT day: held ETF overnight → should earn old ETF return, not money_market."""
        qqq, extra_data, signal_layer, rotation = _make_controlled_backtest_data()

        mock_fastre.return_value = signal_layer
        mock_rotation.return_value = rotation

        config = StrategyConfig(fee_rate=0.001, money_market_annual_rate=0.02)
        result = run_backtest(qqq, config, extra_data=extra_data)

        eq = result.equity_curve["equity"].tolist()

        # Day index 20 is EXIT (sig_idx=19 switches from True→False)
        # The return should include old ETF open-to-open return - fee
        exit_return = eq[20] / eq[19] - 1

        # ETF has ~1% daily return, money_market is ~0.005%
        # Exit return should be ~ETF_return - fee, NOT money_market - fee
        daily_mm = config.money_market_annual_rate / 365
        assert exit_return > daily_mm, (
            f"EXIT day return {exit_return:.6f} should be > money_market {daily_mm:.6f}"
        )

    @patch("strategies.n100_guard_z.signal._fastre_signals")
    @patch("strategies.n100_guard_z.signal._zscore_rotation")
    def test_rotation_day_earns_old_etf_return(self, mock_rotation, mock_fastre):
        """ROTATION day: held old ETF overnight → should earn old ETF return."""
        qqq, extra_data, signal_layer, rotation = _make_rotation_data()

        mock_fastre.return_value = signal_layer
        mock_rotation.return_value = rotation

        config = StrategyConfig(fee_rate=0.001, money_market_annual_rate=0.02)
        result = run_backtest(qqq, config, extra_data=extra_data)

        eq = result.equity_curve["equity"].tolist()

        # Rotation happens at i=16: sig_day=day_16 has shifted_etf=rotation[15]="159941"
        # while prev_etf="513100" → ROTATION. equity[16] = result of this iteration.
        rotation_return = eq[16] / eq[15] - 1

        # Should earn OLD ETF (513100, ~1% daily) return - 2*fee
        # Not new ETF (159941, ~1.5% daily) return
        assert rotation_return < 0.012, (
            f"ROTATION day return {rotation_return:.6f} should reflect old ETF (~1%), not new (~1.5%)"
        )
        assert rotation_return > 0.005, (
            f"ROTATION day return {rotation_return:.6f} should be positive (old ETF return - 2*fee)"
        )

    @patch("strategies.n100_guard_z.signal._fastre_signals")
    @patch("strategies.n100_guard_z.signal._zscore_rotation")
    def test_trade_pnl_uses_actual_etf_prices(self, mock_rotation, mock_fastre):
        """Trade PnL should reflect actual ETF adj_open price movement."""
        qqq, extra_data, signal_layer, rotation = _make_controlled_backtest_data()

        mock_fastre.return_value = signal_layer
        mock_rotation.return_value = rotation

        config = StrategyConfig(fee_rate=0.001, money_market_annual_rate=0.02)
        result = run_backtest(qqq, config, extra_data=extra_data)

        # First trade: entry at day 10, exit at day 20
        assert len(result.trades) >= 1
        trade = result.trades[0]

        # PnL should be based on actual ETF prices, not equity curve
        expected_pnl = (trade.exit_price / trade.entry_price - 1 - 2 * config.fee_rate) * 100
        assert abs(trade.pnl_pct - expected_pnl) < 0.01, (
            f"Trade PnL {trade.pnl_pct:.4f}% != price-based {expected_pnl:.4f}%"
        )

    @patch("strategies.n100_guard_z.signal._fastre_signals")
    @patch("strategies.n100_guard_z.signal._zscore_rotation")
    def test_risk_on_empty_etf_treated_as_cash(self, mock_rotation, mock_fastre):
        """risk_on=True but selected_etf='' → should earn money_market."""
        dates = pd.date_range("2024-01-01", periods=10, freq="D", tz="UTC")
        qqq = pd.DataFrame({"timestamp": dates, "close": np.linspace(100, 110, 10)})
        spy = pd.DataFrame({"timestamp": dates, "close": np.linspace(100, 105, 10)})
        etf_513100 = _make_etf_prices(dates, "513100")
        nav_df = pd.DataFrame({"timestamp": dates, "513100": etf_513100["close"] * 0.998})

        # risk_on=True but no ETF selected
        signal_layer = pd.DataFrame({
            "timestamp": dates,
            "qqq_close": qqq["close"],
            "risk_on": [True] * 10,
            "state": ["NDX_INVESTED"] * 10,
        })
        rotation = pd.DataFrame({"selected_etf": [""] * 10})

        mock_fastre.return_value = signal_layer
        mock_rotation.return_value = rotation

        config = StrategyConfig(fee_rate=0.001, money_market_annual_rate=0.02)
        result = run_backtest(
            qqq, config,
            extra_data={"spy": spy, "etf_513100": etf_513100, "etf_nav": nav_df},
        )

        eq = result.equity_curve["equity"].tolist()
        daily_mm = config.money_market_annual_rate / 365

        # All days should earn money_market (no ETF exposure)
        for i in range(3, len(eq)):
            day_return = eq[i] / eq[i - 1] - 1
            assert abs(day_return - daily_mm) < 1e-8, (
                f"Day {i}: return {day_return:.8f} != money_market {daily_mm:.8f}"
            )

    @patch("strategies.n100_guard_z.signal._fastre_signals")
    @patch("strategies.n100_guard_z.signal._zscore_rotation")
    def test_empty_etf_then_real_etf_triggers_entry(self, mock_rotation, mock_fastre):
        """risk_on=True with empty ETF for days, then ETF appears → should trigger entry."""
        dates = pd.date_range("2024-01-01", periods=15, freq="D", tz="UTC")
        qqq = pd.DataFrame({"timestamp": dates, "close": np.linspace(100, 115, 15)})
        spy = pd.DataFrame({"timestamp": dates, "close": np.linspace(100, 107, 15)})
        etf_513100 = _make_etf_prices(dates, "513100", base=2.0, daily_pct=0.01)
        nav_df = pd.DataFrame({"timestamp": dates, "513100": etf_513100["close"] * 0.998})

        # risk_on=True entire time, but ETF only available from day 5 onward
        signal_layer = pd.DataFrame({
            "timestamp": dates,
            "qqq_close": qqq["close"],
            "risk_on": [True] * 15,
            "state": ["NDX_INVESTED"] * 15,
        })
        selected = [""] * 5 + ["513100"] * 10
        rotation = pd.DataFrame({"selected_etf": selected})

        mock_fastre.return_value = signal_layer
        mock_rotation.return_value = rotation

        config = StrategyConfig(fee_rate=0.001, money_market_annual_rate=0.02)
        result = run_backtest(
            qqq, config,
            extra_data={"spy": spy, "etf_513100": etf_513100, "etf_nav": nav_df},
        )

        eq = result.equity_curve["equity"].tolist()
        daily_mm = config.money_market_annual_rate / 365

        # Days 1-5 (loop i=1..5): risk_on=True but shifted_etf="" → money_market
        # (shift(1) means rotation[4]="" is still used at idx=5)
        for i in range(1, 6):
            day_return = eq[i] / eq[i - 1] - 1
            assert abs(day_return - daily_mm) < 1e-8, (
                f"Day {i}: should earn money_market, got {day_return:.8f}"
            )

        # i=6: shifted_etf = rotation[5] = "513100" → ENTRY (earn mm - fee)
        entry_return = eq[6] / eq[5] - 1
        expected_entry = daily_mm - config.fee_rate
        assert abs(entry_return - expected_entry) < 1e-8, (
            f"ENTRY day return {entry_return:.8f} != expected {expected_entry:.8f}"
        )

        # i=7: HOLD → earn ETF return
        hold_return = eq[7] / eq[6] - 1
        assert hold_return > daily_mm, (
            f"HOLD day return {hold_return:.8f} should be > money_market {daily_mm:.8f}"
        )
