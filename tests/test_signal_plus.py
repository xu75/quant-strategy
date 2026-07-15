"""Tests for TrendLock 40 Plus signal logic (exit_confirm)."""
from __future__ import annotations

import pandas as pd
import pytest

from strategies.btc_ma_trend_plus.signal import (
    StrategyConfig,
    Signal,
    compute_signals,
    get_current_signal,
    _compute_ma_slope,
    _slope_gate_blocks,
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


class TestSlopeGate:
    """MA240 开多斜率门 (effective 2026-07). Uses ma_window=3 fixtures.

    FALLING [200,190,180,170,160,150,175]: only golden cross is at bar 6
    (prev 150 <= MA 160, close 175 > MA 161.67); MA slope over 3 bars is
    161.67/180 - 1 = -10.2% (< -2%, blocked).
    RISING [100,102,104,106,108,90,130]: only cross at bar 6 (prev 90 <=
    MA 101.33, close 130 > MA 109.33); slope 109.33/104 - 1 = +5.1% (kept).
    STEEP_STRADDLE [300,280,260,240,220,250,150,140]: first cross at bar 5
    (prev 220 <= MA 240, close 250 > MA 236.67); slope MA[5]/MA[2]-1 =
    236.67/280-1 = -15.5% (would be blocked when active). Exit at bar 7
    (bars 6,7 close 150,140 < MA -> 2 consecutive below). Used to straddle
    the effective date: entry can be shielded (predates) while exit fires
    after the gate is live -> proves the gate touches only entries.
    """

    FALLING = [200, 190, 180, 170, 160, 150, 175]
    RISING = [100, 102, 104, 106, 108, 90, 130]
    STEEP_STRADDLE = [300, 280, 260, 240, 220, 250, 150, 140]

    def _cfg(self, **kw):
        base = dict(
            ma_window=3, min_hold_bars=1, exit_confirm_bars=2,
            slope_lookback_bars=3, slope_gate_effective_date="",
        )
        base.update(kw)
        return StrategyConfig(**base)

    def test_steep_negative_slope_blocks_entry(self):
        """Golden cross with MA slope < -2% is rejected (0 buys)."""
        df = make_candles(self.FALLING)
        signals = compute_signals(df, self._cfg())
        assert [s for s in signals if s.action == "buy"] == []

    def test_same_cross_passes_when_gate_disabled(self):
        """Disabling the gate lets the same steep cross through (1 buy)."""
        df = make_candles(self.FALLING)
        signals = compute_signals(df, self._cfg(slope_gate_enabled=False))
        buys = [s for s in signals if s.action == "buy"]
        assert len(buys) == 1
        assert buys[0].price == 175

    def test_rising_slope_allows_entry(self):
        """Golden cross with rising MA passes the gate (1 buy)."""
        df = make_candles(self.RISING)
        signals = compute_signals(df, self._cfg())
        buys = [s for s in signals if s.action == "buy"]
        assert len(buys) == 1
        assert buys[0].price == 130
        assert "slope" in buys[0].reason.lower()

    def test_effective_date_shields_historical_crosses(self):
        """A steep cross before the effective date is NOT gated (backtest intact)."""
        df = make_candles(self.FALLING)  # timestamps in 2024
        signals = compute_signals(df, self._cfg(slope_gate_effective_date="2026-07-01"))
        buys = [s for s in signals if s.action == "buy"]
        assert len(buys) == 1  # 2024 cross precedes 2026-07 activation

    def test_insufficient_ma_history_passes_through(self):
        """Cross before slope_lookback bars of history is not gated."""
        df = make_candles(self.FALLING)  # cross at bar 6
        signals = compute_signals(df, self._cfg(slope_lookback_bars=30))
        buys = [s for s in signals if s.action == "buy"]
        assert len(buys) == 1  # ref_idx = 6 - 30 < 0 -> pass through

    def test_blocked_entry_opens_no_position(self):
        """A blocked entry opens no position, so there is nothing to sell.

        Note: this only proves the *consequence* of blocking, NOT that the exit
        rule itself is untouched. See test_gate_live_does_not_block_exit for the
        real contract test where a position is actually held.
        """
        df = make_candles(self.FALLING)
        signals = compute_signals(df, self._cfg())
        assert [s for s in signals if s.action == "buy"] == []
        assert [s for s in signals if s.action == "sell"] == []

    def test_gate_live_does_not_block_exit(self):
        """Real exit contract: enter before the effective date (entry shielded),
        then exit AFTER the gate is live. The entry cross has slope -15.5% and
        WOULD be blocked if it occurred after the date, yet the exit still fires.

        STEEP_STRADDLE @ start 2026-06-30 (4H): entry bar 5 = 06-30 20:00
        (< 2026-07-01, shielded); exit bar 7 = 07-01 04:00 (gate live)."""
        df = make_candles(self.STEEP_STRADDLE, start="2026-06-30")
        signals = compute_signals(df, self._cfg(slope_gate_effective_date="2026-07-01"))
        buys = [s for s in signals if s.action == "buy"]
        sells = [s for s in signals if s.action == "sell"]
        assert len(buys) == 1 and buys[0].price == 250   # shielded entry
        assert len(sells) == 1 and sells[0].price == 140  # exit fires post-activation

    def test_same_straddle_entry_blocked_when_gate_over_all_history(self):
        """Control: with the gate over all history, the same steep entry is
        blocked -> no position -> no exit. Confirms the straddle's entry really
        is gate-eligible (so the shield in the prior test is meaningful)."""
        df = make_candles(self.STEEP_STRADDLE, start="2026-06-30")
        signals = compute_signals(df, self._cfg(slope_gate_effective_date=""))
        assert [s for s in signals if s.action == "buy"] == []
        assert [s for s in signals if s.action == "sell"] == []

    def test_slope_exactly_at_threshold_passes(self):
        """Boundary: slope == threshold must PASS (strict '<' comparison)."""
        # ma[3]/ma[0] - 1 = 75/100 - 1 = -0.25 exactly (binary-exact values)
        ma = pd.Series([100.0, 90.0, 80.0, 75.0])
        cfg = self._cfg(slope_gate_threshold=-0.25, slope_lookback_bars=3)
        blocked, slope = _slope_gate_blocks(
            ma, idx=3, cross_ts=pd.Timestamp("2026-07-01", tz="UTC"),
            config=cfg, effective_ts=None,
        )
        assert slope == pytest.approx(-0.25)
        assert blocked is False

    def test_slope_just_below_threshold_blocks(self):
        """Boundary: slope strictly below threshold must BLOCK."""
        # ma[3]/ma[0] - 1 = 74/100 - 1 = -0.26 < -0.25
        ma = pd.Series([100.0, 90.0, 80.0, 74.0])
        cfg = self._cfg(slope_gate_threshold=-0.25, slope_lookback_bars=3)
        blocked, slope = _slope_gate_blocks(
            ma, idx=3, cross_ts=pd.Timestamp("2026-07-01", tz="UTC"),
            config=cfg, effective_ts=None,
        )
        assert slope == pytest.approx(-0.26)
        assert blocked is True

    def test_effective_date_exact_timestamp_activates(self):
        """Boundary: a cross whose timestamp == effective date IS gated.

        The before-check uses strict '<', so cross_ts == effective_ts does not
        shield. STEEP_STRADDLE @ start 2026-06-30 04:00 -> entry bar 5 lands
        exactly on 2026-07-01 00:00 and must be blocked (slope -15.5%)."""
        df = make_candles(self.STEEP_STRADDLE, start="2026-06-30 04:00")
        signals = compute_signals(df, self._cfg(slope_gate_effective_date="2026-07-01"))
        assert [s for s in signals if s.action == "buy"] == []

    def test_effective_date_one_bar_before_shields(self):
        """Companion: one bar earlier, the entry precedes the effective date
        and is shielded (1 buy) — confirms the boundary is exactly at the date."""
        df = make_candles(self.STEEP_STRADDLE, start="2026-06-30 00:00")
        signals = compute_signals(df, self._cfg(slope_gate_effective_date="2026-07-01"))
        buys = [s for s in signals if s.action == "buy"]
        assert len(buys) == 1 and buys[0].price == 250

    def test_live_signal_blocked_returns_hold(self):
        """get_current_signal returns hold with slope-gate reason on steep cross."""
        df = make_candles(self.FALLING)
        sig = get_current_signal(df, in_position=False, config=self._cfg())
        assert sig.action == "hold"
        assert "slope gate" in sig.reason.lower()

    def test_live_signal_rising_returns_buy(self):
        """get_current_signal still buys when the gate passes."""
        df = make_candles(self.RISING)
        sig = get_current_signal(df, in_position=False, config=self._cfg())
        assert sig.action == "buy"

    def test_live_signal_effective_date_shield(self):
        """Live buy passes when the cross precedes the effective date."""
        df = make_candles(self.FALLING)  # 2024 timestamps
        sig = get_current_signal(
            df, in_position=False, config=self._cfg(slope_gate_effective_date="2026-07-01")
        )
        assert sig.action == "buy"


class TestComputeMaSlope:
    def test_slope_value(self):
        ma = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
        assert _compute_ma_slope(ma, 5, 3) == pytest.approx(105.0 / 102.0 - 1.0)

    def test_insufficient_history_returns_none(self):
        ma = pd.Series([100.0, 101.0, 102.0])
        assert _compute_ma_slope(ma, 2, 3) is None

    def test_nan_reference_returns_none(self):
        ma = pd.Series([float("nan"), 101.0, 102.0, 103.0])
        assert _compute_ma_slope(ma, 3, 3) is None
