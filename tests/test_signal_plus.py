"""Tests for TrendLock 40 Plus signal logic (exit_confirm)."""
from __future__ import annotations

import pandas as pd
import pytest

from strategies.btc_ma_trend_plus.signal import (
    StrategyConfig,
    Signal,
    compute_signals,
    get_current_signal,
)


def make_candles(prices: list, start: str = "2024-01-01") -> pd.DataFrame:
    timestamps = pd.date_range(start, periods=len(prices), freq="4h", tz="UTC")
    return pd.DataFrame({"timestamp": timestamps, "close": prices})


class TestExitConfirm:
    def test_no_sell_on_single_bar_below_ma(self):
        """One bar below MA is not enough with exit_confirm=2."""
        config = StrategyConfig(ma_window=3, min_hold_bars=1, exit_confirm_bars=2)
        # buy at 105 (crossover), then one bar below MA, then back above
        prices = [100, 100, 100, 99, 105, 95, 110]
        df = make_candles(prices)
        signals = compute_signals(df, config)

        sells = [s for s in signals if s.action == "sell"]
        assert len(sells) == 0

    def test_sell_after_two_consecutive_bars_below(self):
        """Two consecutive bars below MA triggers sell with exit_confirm=2."""
        config = StrategyConfig(ma_window=3, min_hold_bars=1, exit_confirm_bars=2)
        prices = [100, 100, 100, 99, 105, 90, 85]
        df = make_candles(prices)
        signals = compute_signals(df, config)

        sells = [s for s in signals if s.action == "sell"]
        assert len(sells) == 1
        assert "consecutive" in sells[0].reason.lower()

    def test_consecutive_reset_on_bar_above(self):
        """Counter resets when price goes back above MA."""
        config = StrategyConfig(ma_window=3, min_hold_bars=1, exit_confirm_bars=2)
        # buy at 105, one below, one above (reset), one below -> no sell
        prices = [100, 100, 100, 99, 105, 90, 110, 90]
        df = make_candles(prices)
        signals = compute_signals(df, config)

        sells = [s for s in signals if s.action == "sell"]
        assert len(sells) == 0

    def test_min_hold_still_enforced(self):
        """Exit confirm doesn't bypass min_hold."""
        config = StrategyConfig(ma_window=3, min_hold_bars=5, exit_confirm_bars=2)
        # buy at 105, immediately 2 bars below but min_hold=5 not met
        prices = [100, 100, 100, 99, 105, 90, 85, 80, 75]
        df = make_candles(prices)
        signals = compute_signals(df, config)

        sells = [s for s in signals if s.action == "sell"]
        # sell should only happen after bar 5 from entry
        for s in sells:
            assert s.hold_bars >= 5

    def test_exit_confirm_3(self):
        """exit_confirm=3 requires three consecutive bars below."""
        config = StrategyConfig(ma_window=3, min_hold_bars=1, exit_confirm_bars=3)
        # buy at 105, two bars below (not enough), then third below -> sell
        prices = [100, 100, 100, 99, 105, 90, 85, 80]
        df = make_candles(prices)
        signals = compute_signals(df, config)

        sells = [s for s in signals if s.action == "sell"]
        assert len(sells) == 1

    def test_exit_confirm_3_not_enough_with_two(self):
        """Two bars below is not enough for exit_confirm=3."""
        config = StrategyConfig(ma_window=3, min_hold_bars=1, exit_confirm_bars=3)
        prices = [100, 100, 100, 99, 105, 90, 85, 110]
        df = make_candles(prices)
        signals = compute_signals(df, config)

        sells = [s for s in signals if s.action == "sell"]
        assert len(sells) == 0

    def test_entry_logic_unchanged(self):
        """Entry is still crossover, not affected by exit_confirm."""
        config = StrategyConfig(ma_window=3, min_hold_bars=1, exit_confirm_bars=2)
        prices = [100, 100, 100, 99, 105]
        df = make_candles(prices)
        signals = compute_signals(df, config)

        buys = [s for s in signals if s.action == "buy"]
        assert len(buys) == 1
        assert buys[0].price == 105

    def test_multiple_round_trips(self):
        """Can do multiple buy/sell cycles with exit_confirm."""
        config = StrategyConfig(ma_window=3, min_hold_bars=1, exit_confirm_bars=2)
        prices = [100, 100, 100]
        prices.extend([99, 110, 85, 80])  # buy 110, sell at 80 (2 bars below)
        prices.extend([78, 79, 77, 95])   # below MA, then crossover buy at 95
        prices.extend([80, 75])            # 2 bars below -> sell
        df = make_candles(prices)
        signals = compute_signals(df, config)

        buys = [s for s in signals if s.action == "buy"]
        sells = [s for s in signals if s.action == "sell"]
        assert len(buys) >= 2
        assert len(sells) >= 2


class TestGetCurrentSignalPlus:
    def test_hold_with_partial_exit_confirm(self):
        """One bar below MA -> hold, showing confirm progress."""
        config = StrategyConfig(ma_window=3, min_hold_bars=1, exit_confirm_bars=2)
        prices = [100, 100, 100, 99, 105, 90]
        df = make_candles(prices)
        signal = get_current_signal(df, in_position=True, entry_bar_idx=4, config=config)
        assert signal.action == "hold"
        assert "1/2" in signal.reason

    def test_sell_with_full_exit_confirm(self):
        """Two bars below MA -> sell."""
        config = StrategyConfig(ma_window=3, min_hold_bars=1, exit_confirm_bars=2)
        prices = [100, 100, 100, 99, 105, 90, 85]
        df = make_candles(prices)
        signal = get_current_signal(df, in_position=True, entry_bar_idx=4, config=config)
        assert signal.action == "sell"

    def test_buy_crossover_unchanged(self):
        """Buy signal detection unchanged from base strategy."""
        config = StrategyConfig(ma_window=3, min_hold_bars=1, exit_confirm_bars=2)
        prices = [100, 100, 100, 99, 105]
        df = make_candles(prices)
        signal = get_current_signal(df, in_position=False, config=config)
        assert signal.action == "buy"

    def test_min_hold_not_met(self):
        config = StrategyConfig(ma_window=3, min_hold_bars=10, exit_confirm_bars=2)
        prices = [100, 100, 100, 99, 105, 90]
        df = make_candles(prices)
        signal = get_current_signal(df, in_position=True, entry_bar_idx=4, config=config)
        assert signal.action == "hold"
        assert "min hold" in signal.reason.lower()
