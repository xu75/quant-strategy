"""Tests for BTC MA trend strategy signal logic."""

import pandas as pd
import pytest

from strategies.btc_ma_trend.signal import (
    StrategyConfig,
    Signal,
    compute_signals,
    get_current_signal,
)


def make_candles(prices: list[float], start: str = "2024-01-01") -> pd.DataFrame:
    """Helper: create a DataFrame from a list of close prices."""
    timestamps = pd.date_range(start, periods=len(prices), freq="4h", tz="UTC")
    return pd.DataFrame({"timestamp": timestamps, "close": prices})


class TestComputeSignals:
    def test_no_signals_when_insufficient_data(self):
        config = StrategyConfig(ma_window=5, min_hold_bars=2)
        df = make_candles([100, 101, 102])  # less than ma_window + 1
        signals = compute_signals(df, config)
        assert signals == []

    def test_buy_signal_on_crossover(self):
        """Price crosses from below MA to above MA -> buy."""
        config = StrategyConfig(ma_window=3, min_hold_bars=1)
        # MA(3) of [100, 100, 100] = 100
        # Then price goes 99 (below MA), then 105 (above MA) -> crossover
        prices = [100, 100, 100, 99, 105, 90]  # buy at 105, sell at 90 after 1 bar
        df = make_candles(prices)
        signals = compute_signals(df, config)

        assert len(signals) >= 1
        assert signals[0].action == "buy"
        assert signals[0].price == 105

    def test_sell_after_min_hold(self):
        """Sell only happens after minimum hold period."""
        config = StrategyConfig(ma_window=3, min_hold_bars=2)
        # Setup: cross above MA, then immediately below, then below again
        # Should not sell until after 2 bars
        prices = [100, 100, 100, 99, 105]  # buy at 105
        # Add bars: hold bar 1 (below MA but can't sell), hold bar 2 (below -> sell)
        prices.extend([90, 85])
        df = make_candles(prices)
        signals = compute_signals(df, config)

        buys = [s for s in signals if s.action == "buy"]
        sells = [s for s in signals if s.action == "sell"]

        assert len(buys) == 1
        assert len(sells) == 1
        assert sells[0].hold_bars >= 2

    def test_no_sell_before_min_hold(self):
        """Price drops below MA but min hold not met -> no sell."""
        config = StrategyConfig(ma_window=3, min_hold_bars=10)
        prices = [100, 100, 100, 99, 105, 90, 85, 80]
        df = make_candles(prices)
        signals = compute_signals(df, config)

        sells = [s for s in signals if s.action == "sell"]
        assert len(sells) == 0  # min_hold=10 never reached

    def test_asymmetric_entry_exit(self):
        """Entry requires crossover (prev<=MA, curr>MA).
        Exit just needs price < MA (no crossunder required)."""
        config = StrategyConfig(ma_window=3, min_hold_bars=1)
        # Entry: prev below MA, current above -> crossover
        # Exit: just price below MA (no requirement that prev was above)
        prices = [100, 100, 100, 99, 105, 95]
        df = make_candles(prices)
        signals = compute_signals(df, config)

        buys = [s for s in signals if s.action == "buy"]
        sells = [s for s in signals if s.action == "sell"]

        assert len(buys) >= 1
        # The sell should trigger because price < MA, not because of crossunder
        if len(sells) > 0:
            assert sells[0].price < sells[0].ma_value

    def test_multiple_round_trips(self):
        """Strategy can produce multiple buy/sell pairs."""
        config = StrategyConfig(ma_window=3, min_hold_bars=1)
        # Trip 1: cross above then below
        # Trip 2: cross above then below
        prices = [100, 100, 100]
        # Trip 1
        prices.extend([99, 110, 85])  # buy 110, sell 85
        # Recovery - need price to drop below MA and then cross above again
        prices.extend([86, 87, 88, 85, 95])  # buy at 95 (crossover)
        prices.extend([80])  # sell at 80
        df = make_candles(prices)
        signals = compute_signals(df, config)

        buys = [s for s in signals if s.action == "buy"]
        sells = [s for s in signals if s.action == "sell"]

        assert len(buys) >= 2
        assert len(sells) >= 2


class TestGetCurrentSignal:
    def test_no_position_above_ma(self):
        config = StrategyConfig(ma_window=3, min_hold_bars=1)
        prices = [100, 100, 100, 101, 102]  # price above MA
        df = make_candles(prices)
        signal = get_current_signal(df, in_position=False, config=config)
        assert signal.action == "hold"
        assert "above" in signal.reason.lower()

    def test_no_position_below_ma(self):
        config = StrategyConfig(ma_window=3, min_hold_bars=1)
        prices = [100, 100, 100, 99, 98]
        df = make_candles(prices)
        signal = get_current_signal(df, in_position=False, config=config)
        assert signal.action == "hold"
        assert "below" in signal.reason.lower()

    def test_in_position_min_hold_not_met(self):
        config = StrategyConfig(ma_window=3, min_hold_bars=10)
        prices = [100, 100, 100, 99, 105, 90]
        df = make_candles(prices)
        signal = get_current_signal(df, in_position=True, entry_bar_idx=4, config=config)
        assert signal.action == "hold"
        assert "min hold" in signal.reason.lower()

    def test_buy_crossover_detected(self):
        config = StrategyConfig(ma_window=3, min_hold_bars=1)
        prices = [100, 100, 100, 99, 105]
        df = make_candles(prices)
        signal = get_current_signal(df, in_position=False, config=config)
        assert signal.action == "buy"
