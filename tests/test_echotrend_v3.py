"""EchoTrend 240 V3 — verification tests.

Covers the V3-specific semantics required by the upgrade spec:
1. Decision-row (current-bar open) execution, scoring uses prev bar
2. Asymmetric confirm cadence (bear=3, bull=9 on MSTR 1H)
3. Circuit breaker (prev bar high→low vs intraday_high, -10%, reset next day)
4. Crash mode 1-bar confirm (mode label, CAP1.0 prevents position change)
5. Current equity sizing (not fixed initial_shares)
6. Strong reentry (3/5 votes → step_up=0.90 during regime flip)
7. No lookahead: V6 scoring uses previous completed bar
"""

import pytest
import pandas as pd

from strategies.echotrend_240_v3.engine import EchoTrendV3Engine, PortfolioState
from strategies.echotrend_240_v3.signal import _build_engine_config, StrategyConfig


def _prod_config():
    """Use production config to prevent test/prod drift."""
    return _build_engine_config(StrategyConfig())


def _base_row(**overrides):
    row = {
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
        "volume": 1000, "btc_4h_above_sma240": 1.0,
        "mstr_1h_return": 0.0, "mstr_4h_return": 0.0,
        "mstr_daily_5d_return": 0.0, "btc_1h_return": 0.0,
        "btc_daily_5d_return": 0.0, "market_1h_return": 0.0,
        "market_daily_5d_return": 0.0, "mstr_btc_rs_4h": 0.0,
        "day_range": 0.02, "vwap": 100.0,
        "mstr_btc_rs_1h": 0.0, "mstr_btc_ratio_20": 0.0,
        "mstr_daily_20d_return": 0.0, "mstr_daily_above_ema20": 1.0,
        "mstr_daily_above_ema50": 1.0, "btc_daily_20d_return": 0.0,
        "market_daily_above_ema20": 1.0, "rsi": 50.0, "vwap_deviation": 0.0,
    }
    row.update(overrides)
    return pd.Series(row)


class TestNoLookahead:
    """V6 scoring uses prev_row; current bar only provides open for execution."""

    def test_crash_indicators_in_current_bar_do_not_cause_immediate_sell(self):
        engine = EchoTrendV3Engine(_prod_config())
        state = PortfolioState(cash=0.0, shares=100.0, initial_capital=10000.0)

        # Bar 1: normal (becomes prev_row)
        row1 = _base_row()
        dt1 = pd.Timestamp("2024-01-15 10:00:00", tz="US/Eastern")
        engine.on_bar(state, dt1, row1)

        # Bar 2: extreme crash indicators, but scoring uses prev_row (normal)
        row2 = _base_row(
            mstr_1h_return=-0.05, mstr_4h_return=-0.08,
            mstr_daily_5d_return=-0.12, btc_1h_return=-0.03,
            btc_daily_5d_return=-0.07, market_1h_return=-0.01,
            market_daily_5d_return=-0.04, mstr_btc_rs_4h=-0.05,
            day_range=0.10, close=96.0,
        )
        dt2 = pd.Timestamp("2024-01-15 11:00:00", tz="US/Eastern")
        engine.on_bar(state, dt2, row2)

        assert state.shares == 100.0
        assert len(engine.rebalances) == 0

    def test_crash_mode_enters_on_next_bar_after_indicators(self):
        """Crash mode requires 1 bar with score>=70 (using prev_row). Confirmed by backtest."""
        engine = EchoTrendV3Engine(_prod_config())
        state = PortfolioState(cash=0.0, shares=100.0, initial_capital=10000.0)

        crash_row = _base_row(
            mstr_1h_return=-0.05, mstr_4h_return=-0.08,
            mstr_daily_5d_return=-0.12, btc_1h_return=-0.03,
            btc_daily_5d_return=-0.07, market_1h_return=-0.01,
            market_daily_5d_return=-0.04, mstr_btc_rs_4h=-0.05,
            day_range=0.10, close=96.0,
        )

        # Bar 1: normal (becomes prev_row)
        row1 = _base_row()
        engine.on_bar(state, pd.Timestamp("2024-01-15 10:00:00", tz="US/Eastern"), row1)

        # Bar 2: crash indicators (becomes prev_row for bar 3)
        engine.on_bar(state, pd.Timestamp("2024-01-15 11:00:00", tz="US/Eastern"), crash_row)
        # Bar 2 scores bar1 (normal) — no crash yet
        assert engine.mode != "crash"

        # Bar 3: daily fields persist (shift(1) of yesterday), intraday from prev_row (crash)
        # decision_row = prev_row(crash intraday) + current_row(crash daily overlay)
        row3 = _base_row(open=96.0, close=96.0,
                         mstr_daily_5d_return=-0.12, btc_daily_5d_return=-0.07,
                         market_daily_5d_return=-0.04)
        engine.on_bar(state, pd.Timestamp("2024-01-15 12:00:00", tz="US/Eastern"), row3)

        assert engine.mode == "crash"


class TestDecisionRowOverlay:
    """decision_row = prev_row intraday + current_row daily overlay."""

    def test_daily_overlay_triggers_crash_on_first_rth_bar(self):
        """First RTH bar of a crash day: prev_row is normal intraday (end of prev day),
        but current_row carries crash-level daily fields (shift(1) of yesterday's close).
        decision_row should combine both → crash score >= 70."""
        engine = EchoTrendV3Engine(_prod_config())
        state = PortfolioState(cash=0.0, shares=100.0, initial_capital=10000.0)

        # Bar 1 (prev day last bar): normal intraday, normal daily
        row1 = _base_row()
        engine.on_bar(state, pd.Timestamp("2024-01-15 15:00:00", tz="US/Eastern"), row1)

        # Bar 2 (today first RTH bar): normal intraday in prev_row,
        # but current_row has crash-level daily fields (overnight gap reflected in daily)
        # Also prev_row has crash-level intraday from overnight
        crash_prev = _base_row(
            mstr_1h_return=-0.05, mstr_4h_return=-0.08,
            btc_1h_return=-0.03, market_1h_return=-0.01,
            mstr_btc_rs_4h=-0.05, day_range=0.10, close=96.0,
        )
        engine.on_bar(state, pd.Timestamp("2024-01-16 09:30:00", tz="US/Eastern"), crash_prev)

        # Bar 3: prev_row has crash intraday; current_row overlays crash daily
        row3 = _base_row(
            open=94.0, close=94.0,
            mstr_daily_5d_return=-0.12, btc_daily_5d_return=-0.07,
            market_daily_5d_return=-0.04,
        )
        engine.on_bar(state, pd.Timestamp("2024-01-16 10:30:00", tz="US/Eastern"), row3)

        assert engine.mode == "crash"

    def test_normal_daily_overlay_prevents_false_crash(self):
        """If current_row daily fields are normal, crash should NOT trigger
        even if prev_row intraday looks bad — daily overlay dilutes the score."""
        engine = EchoTrendV3Engine(_prod_config())
        state = PortfolioState(cash=0.0, shares=100.0, initial_capital=10000.0)

        # Bar 1: normal
        engine.on_bar(state, pd.Timestamp("2024-01-15 10:00:00", tz="US/Eastern"), _base_row())

        # Bar 2: bad intraday only (no daily crash)
        row2 = _base_row(
            mstr_1h_return=-0.05, mstr_4h_return=-0.08,
            btc_1h_return=-0.03, market_1h_return=-0.01,
            mstr_btc_rs_4h=-0.05, day_range=0.10, close=96.0,
            mstr_daily_5d_return=0.0, btc_daily_5d_return=0.0,
            market_daily_5d_return=0.0,
        )
        engine.on_bar(state, pd.Timestamp("2024-01-15 11:00:00", tz="US/Eastern"), row2)

        # Bar 3: current_row has normal daily → overlay replaces with 0.0
        # decision_row intraday from prev_row (crash) but daily from current (normal)
        # Score: mstr_1h(-0.05<-0.035)=15 + mstr_4h(-0.08<-0.06)=15 + btc_1h(-0.03<-0.02)=10
        #       + market_1h(-0.01<-0.008)=10 + mstr_btc_rs_4h(-0.05<-0.04)=10 = 60 < 70
        row3 = _base_row(open=96.0, close=96.0,
                         mstr_daily_5d_return=0.0, btc_daily_5d_return=0.0,
                         market_daily_5d_return=0.0)
        engine.on_bar(state, pd.Timestamp("2024-01-15 12:00:00", tz="US/Eastern"), row3)

        assert engine.mode != "crash"


class TestDecisionRowExecution:
    """V3 executes at current bar's open, not next bar."""

    def test_regime_flip_fills_at_current_open(self):
        engine = EchoTrendV3Engine(_prod_config())
        state = PortfolioState(cash=10000.0, shares=0.0, initial_capital=10000.0)

        # Need prev_row first, then 3 bear bars to confirm
        rows_bear = []
        for i in range(4):
            rows_bear.append(_base_row(btc_4h_above_sma240=0.0, open=100.0))
        for i, row in enumerate(rows_bear):
            dt = pd.Timestamp(f"2024-01-15 {10+i}:00:00", tz="US/Eastern")
            engine.on_bar(state, dt, row)

        assert engine.trend_regime == "bear"

        # Now 10 bull bars (1 for prev_row + 9 to confirm)
        for i in range(10):
            row = _base_row(btc_4h_above_sma240=1.0, open=50.0, high=51.0, low=49.0, close=50.0)
            dt = pd.Timestamp(f"2024-01-16 {10+i}:00:00", tz="US/Eastern")
            engine.on_bar(state, dt, row)

        assert engine.trend_regime == "bull"
        buys = [r for r in engine.rebalances if r.side == "buy"]
        assert len(buys) > 0
        # Fill price based on open (50.0 + slippage)
        assert buys[-1].price == pytest.approx(50.0 * 1.0005, rel=1e-4)


class TestConfirmCadence:
    """Bear=3, bull=9 on MSTR 1H bars."""

    def test_bear_confirms_in_3_bars(self):
        engine = EchoTrendV3Engine(_prod_config())
        state = PortfolioState(cash=0.0, shares=100.0, initial_capital=10000.0)
        assert engine.trend_regime == "bull"

        # Bar 0: establishes prev_row
        row0 = _base_row(btc_4h_above_sma240=1.0)
        engine.on_bar(state, pd.Timestamp("2024-01-15 09:00:00", tz="US/Eastern"), row0)

        # 2 bars below — not yet confirmed
        for i in range(2):
            row = _base_row(btc_4h_above_sma240=0.0)
            dt = pd.Timestamp(f"2024-01-15 {10+i}:00:00", tz="US/Eastern")
            engine.on_bar(state, dt, row)
        assert engine.trend_regime == "bull"

        # 3rd bar — confirmed
        row = _base_row(btc_4h_above_sma240=0.0)
        dt = pd.Timestamp("2024-01-15 12:00:00", tz="US/Eastern")
        engine.on_bar(state, dt, row)
        assert engine.trend_regime == "bear"

    def test_bull_requires_9_bars(self):
        engine = EchoTrendV3Engine(_prod_config())
        state = PortfolioState(cash=10000.0, shares=0.0, initial_capital=10000.0)

        # Force bear first (1 prev_row + 3 confirm)
        for i in range(4):
            row = _base_row(btc_4h_above_sma240=0.0)
            dt = pd.Timestamp(f"2024-01-15 {10+i}:00:00", tz="US/Eastern")
            engine.on_bar(state, dt, row)
        assert engine.trend_regime == "bear"

        # 8 bars above — not yet confirmed
        for i in range(8):
            row = _base_row(btc_4h_above_sma240=1.0)
            dt = pd.Timestamp(f"2024-01-16 {10+i}:00:00", tz="US/Eastern")
            engine.on_bar(state, dt, row)
        assert engine.trend_regime == "bear"

        # 9th bar — confirmed
        row = _base_row(btc_4h_above_sma240=1.0)
        dt = pd.Timestamp("2024-01-16 18:00:00", tz="US/Eastern")
        engine.on_bar(state, dt, row)
        assert engine.trend_regime == "bull"


class TestCircuitBreaker:
    """CB sets target=0, sells via gap*step (not force_exit)."""

    def test_cb_triggers_on_prev_bar_drop(self):
        engine = EchoTrendV3Engine(_prod_config())
        state = PortfolioState(cash=0.0, shares=100.0, initial_capital=10000.0)

        # Bar 1: high=110, establishes intraday_high
        row1 = _base_row(open=100.0, high=110.0, low=100.0, close=105.0)
        dt1 = pd.Timestamp("2024-01-15 10:00:00", tz="US/Eastern")
        engine.on_bar(state, dt1, row1)
        assert not engine.cb_triggered

        # Bar 2: low=85 → prev_bar_low will be used by bar 3
        row2 = _base_row(open=104.0, high=106.0, low=85.0, close=90.0)
        dt2 = pd.Timestamp("2024-01-15 11:00:00", tz="US/Eastern")
        engine.on_bar(state, dt2, row2)
        # CB checks prev_bar (bar1): low=100 vs intraday_high=110 → -9.1% → no trigger
        assert not engine.cb_triggered

        # Bar 3: CB checks prev_bar (bar2): low=85 vs intraday_high=110 → -22.7% → triggers!
        row3 = _base_row(open=87.0, high=88.0, low=84.0, close=85.0)
        dt3 = pd.Timestamp("2024-01-15 12:00:00", tz="US/Eastern")
        engine.on_bar(state, dt3, row3)
        assert engine.cb_triggered
        # CB sells via step=0.50 (not full exit) — partial sell on first bar
        assert state.shares < 100.0
        assert state.shares > 0.0
        sells = [r for r in engine.rebalances if r.side == "sell"]
        assert len(sells) > 0
        assert sells[-1].reason == "v3_reduce_neutral"

    def test_cb_continues_selling_until_zero(self):
        """CB keeps target=0 each bar until position is fully unwound."""
        engine = EchoTrendV3Engine(_prod_config())
        state = PortfolioState(cash=0.0, shares=100.0, initial_capital=10000.0)

        # Bar 1: high=110
        row1 = _base_row(open=100.0, high=110.0, low=100.0, close=105.0)
        engine.on_bar(state, pd.Timestamp("2024-01-15 10:00:00", tz="US/Eastern"), row1)

        # Bar 2: low=85
        row2 = _base_row(open=104.0, high=106.0, low=85.0, close=90.0)
        engine.on_bar(state, pd.Timestamp("2024-01-15 11:00:00", tz="US/Eastern"), row2)

        # Bars 3-12: CB active, keeps selling via step=0.50
        for i in range(10):
            row = _base_row(open=87.0, high=88.0, low=84.0, close=85.0)
            engine.on_bar(state, pd.Timestamp(f"2024-01-15 {12+i}:00:00", tz="US/Eastern"), row)

        assert engine.cb_triggered
        # After 10 bars of step=0.50 selling, position should be near zero
        assert state.shares < 5.0

    def test_cb_does_not_use_current_bar_low(self):
        """Current bar's low is unknown at decision time."""
        engine = EchoTrendV3Engine(_prod_config())
        state = PortfolioState(cash=0.0, shares=100.0, initial_capital=10000.0)

        # Bar 1: normal, high=105
        row1 = _base_row(open=100.0, high=105.0, low=100.0, close=103.0)
        dt1 = pd.Timestamp("2024-01-15 10:00:00", tz="US/Eastern")
        engine.on_bar(state, dt1, row1)

        # Bar 2: current bar has extreme low=50, but prev_bar_low=100
        # CB check: prev_bar_low=100 vs intraday_high=105 → -4.76% → no trigger
        row2 = _base_row(open=102.0, high=103.0, low=50.0, close=55.0)
        dt2 = pd.Timestamp("2024-01-15 11:00:00", tz="US/Eastern")
        engine.on_bar(state, dt2, row2)
        assert not engine.cb_triggered
        assert state.shares == 100.0

    def test_cb_resets_next_day(self):
        engine = EchoTrendV3Engine(_prod_config())
        state = PortfolioState(cash=0.0, shares=100.0, initial_capital=10000.0)

        # Bar 1: high=110
        row1 = _base_row(open=100.0, high=110.0, low=100.0, close=105.0)
        engine.on_bar(state, pd.Timestamp("2024-01-15 10:00:00", tz="US/Eastern"), row1)

        # Bar 2: low=85
        row2 = _base_row(open=104.0, high=106.0, low=85.0, close=90.0)
        engine.on_bar(state, pd.Timestamp("2024-01-15 11:00:00", tz="US/Eastern"), row2)

        # Bar 3: triggers CB
        row3 = _base_row(open=87.0, high=88.0, low=84.0, close=85.0)
        engine.on_bar(state, pd.Timestamp("2024-01-15 12:00:00", tz="US/Eastern"), row3)
        assert engine.cb_triggered

        # Next day — should reset
        row4 = _base_row(open=86.0, high=88.0, low=85.0, close=87.0)
        engine.on_bar(state, pd.Timestamp("2024-01-16 10:00:00", tz="US/Eastern"), row4)
        assert not engine.cb_triggered


class TestCrashMode:
    """Crash mode confirms in 1 bar (backtest-verified). Step_down=0.80 affects sell speed."""

    def test_crash_mode_1_bar_confirm(self):
        engine = EchoTrendV3Engine(_prod_config())
        state = PortfolioState(cash=0.0, shares=100.0, initial_capital=10000.0)

        crash_row = _base_row(
            mstr_1h_return=-0.05, mstr_4h_return=-0.08,
            mstr_daily_5d_return=-0.12, btc_1h_return=-0.03,
            btc_daily_5d_return=-0.07, market_1h_return=-0.01,
            market_daily_5d_return=-0.04, mstr_btc_rs_4h=-0.05,
            day_range=0.10, close=96.0,
        )

        # Bar 1: normal (prev_row)
        engine.on_bar(state, pd.Timestamp("2024-01-15 10:00:00", tz="US/Eastern"), _base_row())

        # Bar 2: crash indicators (becomes prev_row for bar 3)
        engine.on_bar(state, pd.Timestamp("2024-01-15 11:00:00", tz="US/Eastern"), crash_row)
        # Bar 2 scores bar1 (normal) — no crash
        assert engine.mode != "crash"

        # Bar 3: daily fields persist during crash day, intraday from prev_row
        engine.on_bar(state, pd.Timestamp("2024-01-15 12:00:00", tz="US/Eastern"),
                      _base_row(open=96.0, close=96.0,
                                mstr_daily_5d_return=-0.12, btc_daily_5d_return=-0.07,
                                market_daily_5d_return=-0.04))
        assert engine.mode == "crash"

    def test_crash_mode_does_not_trigger_on_normal_prev_row(self):
        """If prev_row is normal (score<70), crash mode does not enter."""
        engine = EchoTrendV3Engine(_prod_config())
        state = PortfolioState(cash=0.0, shares=100.0, initial_capital=10000.0)

        # Bar 1: normal
        engine.on_bar(state, pd.Timestamp("2024-01-15 10:00:00", tz="US/Eastern"), _base_row())
        # Bar 2: normal (scores bar1 normal)
        engine.on_bar(state, pd.Timestamp("2024-01-15 11:00:00", tz="US/Eastern"), _base_row())

        assert engine.mode != "crash"


class TestCurrentEquitySizing:
    """Position sizing uses current equity, not fixed initial_shares."""

    def test_buy_size_scales_with_equity(self):
        engine = EchoTrendV3Engine(_prod_config())
        # Start with appreciated equity
        state = PortfolioState(cash=5000.0, shares=100.0, initial_capital=10000.0)
        # At price=150, equity = 5000 + 100*150 = 20000

        # Force bear: need enough bars to confirm AND fully unwind via step=0.50
        # 1 prev_row + 3 confirm + extra bars for step-based selling
        for i in range(10):
            row = _base_row(btc_4h_above_sma240=0.0, open=150.0, high=151.0, low=149.0, close=150.0)
            dt = pd.Timestamp(f"2024-01-15 {10+i}:00:00", tz="US/Eastern")
            engine.on_bar(state, dt, row)

        assert engine.trend_regime == "bear"
        # Step=0.50 means each bar sells 50% of remaining gap — converges toward zero
        assert state.shares < 10.0

        # Now flip to bull (10 bars: 1 prev + 9 confirm + continued buying via step=0.55)
        bull_dts = []
        for i in range(15):
            day = 16 + i // 7
            hour = 10 + i % 7
            bull_dts.append(pd.Timestamp(f"2024-01-{day} {hour}:00:00", tz="US/Eastern"))
        for dt in bull_dts:
            row = _base_row(btc_4h_above_sma240=1.0, open=150.0, high=151.0, low=149.0, close=150.0)
            engine.on_bar(state, dt, row)

        assert engine.trend_regime == "bull"
        # Should buy based on current equity (~20000), not initial 10000
        # With step=0.55 over multiple bars, should accumulate well above 100 shares
        assert state.shares > 100.0


class TestStrongReentry:
    """3/5 votes → step_up=0.90 during gap-based execution."""

    def test_reentry_accelerates_buy_step(self):
        """With partial position in bull, reentry votes increase step from 0.55 to 0.90."""
        engine = EchoTrendV3Engine(_prod_config())
        # Partially invested — gap exists between current exposure and target=1.0
        state = PortfolioState(cash=5000.0, shares=50.0, initial_capital=10000.0)
        engine.trend_regime = "bull"
        engine.exposure_ema = 0.5

        # Bar 1: normal (prev_row)
        row1 = _base_row(open=100.0, close=100.0)
        engine.on_bar(state, pd.Timestamp("2024-01-15 10:00:00", tz="US/Eastern"), row1)

        # Bar 2: reentry conditions (will be prev_row for bar 3)
        row2 = _base_row(
            mstr_1h_return=0.04, btc_1h_return=0.015,
            market_1h_return=0.005, mstr_btc_rs_1h=0.015,
            close=101.0, vwap=100.0, open=100.0,
        )
        engine.on_bar(state, pd.Timestamp("2024-01-15 11:00:00", tz="US/Eastern"), row2)

        shares_before = state.shares

        # Bar 3: scoring uses bar 2 (reentry conditions met) → step_up=0.90
        row3 = _base_row(open=100.0, close=100.0)
        engine.on_bar(state, pd.Timestamp("2024-01-15 12:00:00", tz="US/Eastern"), row3)

        # Should have bought with step=0.90 (larger than default 0.55)
        buys = [r for r in engine.rebalances if r.side == "buy"]
        assert len(buys) > 0
        assert state.shares > shares_before


class TestIncrementalParity:
    """Export/import + 1 new bar must match full run to same point."""

    def test_incremental_matches_full_run(self):
        config = _prod_config()

        # Build a sequence: 5 normal bars then 1 crash bar
        bars = []
        dts = []
        for i in range(5):
            bars.append(_base_row(btc_4h_above_sma240=1.0, open=100.0 + i))
            dts.append(pd.Timestamp(f"2024-01-15 {10+i}:00:00", tz="US/Eastern"))
        # Bar 6: crash indicators
        bars.append(_base_row(
            mstr_1h_return=-0.05, mstr_4h_return=-0.08,
            mstr_daily_5d_return=-0.12, btc_1h_return=-0.03,
            btc_daily_5d_return=-0.07, market_1h_return=-0.01,
            market_daily_5d_return=-0.04, mstr_btc_rs_4h=-0.05,
            day_range=0.10, close=96.0, open=98.0,
        ))
        dts.append(pd.Timestamp("2024-01-15 15:00:00", tz="US/Eastern"))

        # Full run: all 6 bars
        engine_full = EchoTrendV3Engine(config)
        state_full = PortfolioState(cash=0.0, shares=100.0, initial_capital=10000.0)
        for dt, row in zip(dts, bars):
            engine_full.on_bar(state_full, dt, row)

        # Incremental: run 5 bars, export, import, run bar 6
        engine_inc = EchoTrendV3Engine(config)
        state_inc = PortfolioState(cash=0.0, shares=100.0, initial_capital=10000.0)
        for dt, row in zip(dts[:5], bars[:5]):
            engine_inc.on_bar(state_inc, dt, row)

        snapshot = engine_inc.export_state(state_inc, dts[4])

        # New engine from snapshot
        engine_resume = EchoTrendV3Engine(config)
        state_resume = engine_resume.import_state(snapshot)
        # Reconstruct prev_row (same as run_incremental does)
        engine_resume.prev_row = bars[4]

        engine_resume.on_bar(state_resume, dts[5], bars[5])

        # Parity checks
        assert engine_full.mode == engine_resume.mode
        assert engine_full.trend_regime == engine_resume.trend_regime
        assert engine_full.cb_triggered == engine_resume.cb_triggered
        assert state_full.shares == state_resume.shares
        assert state_full.cash == pytest.approx(state_resume.cash, rel=1e-6)
        assert len(engine_full.rebalances) == len(engine_resume.rebalances)
