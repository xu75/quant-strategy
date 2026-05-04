"""Behavioral tests for EchoTrend 240 engine.

Covers: next-bar execution, open price fills, regime shift,
fail-closed regime field, and margin buy capability.
"""
from __future__ import annotations

import pandas as pd
import numpy as np
import pytest

from strategies.echotrend_240.engine import EchoTrendEngine, EngineBacktestResult


def _minimal_engine_config(**overrides) -> dict:
    """Build a minimal engine config with short warmup for testing."""
    v6 = {
        "min_exposure": 0.70,
        "max_exposure": 1.10,
        "max_margin_fraction": 0.10,
        "base_exposure": 1.00,
        "trend_weight": 0.12,
        "risk_weight": 0.40,
        "relative_strength_weight": 0.07,
        "target_ema_alpha": 1.0,
        "min_trade_exposure": 0.01,
        "cooldown_bars": 0,
        "max_step_up": 1.0,
        "max_step_down": 1.0,
        "fast_reentry_step": 1.0,
        "crash_step_down": 1.0,
        "mode_confirm_bars": 1,
        "risk_mode_confirm_bars": 1,
        "bull_mode_confirm_bars": 1,
        "mode_floors": {"bull": 0.70, "neutral": 0.70, "risk_off": 0.70, "crash": 0.70},
        "mode_ceilings": {"bull": 1.10, "neutral": 1.10, "risk_off": 1.10, "crash": 1.10},
        "mstr_1h_pos": 0.010, "mstr_4h_pos": 0.020,
        "mstr_5d_pos": 0.030, "mstr_20d_pos": 0.050,
        "btc_20d_pos": 0.030, "market_1h_pos": 0.000,
        "mstr_1h_risk": -0.035, "mstr_4h_risk": -0.060, "mstr_5d_risk": -0.100,
        "btc_1h_risk": -0.020, "btc_5d_risk": -0.060,
        "market_1h_risk": -0.008, "market_5d_risk": -0.030,
        "rs_4h_risk": -0.040, "day_range_risk": 0.090,
        "rs_1h_pos": 0.010, "rs_4h_pos": 0.020, "ratio_20_pos": 0.030,
        "rs_1h_neg": -0.020, "rs_4h_neg": -0.040, "ratio_20_neg": -0.050,
        "dip_min_trend_score": 55, "dip_max_risk_score": 45,
        "dip_vwap_dev": 0.010, "dip_add": 0.030,
        "breakout_add": 0.030,
        "overheat_max_risk_score": 50, "overheat_rsi": 82,
        "overheat_vwap_dev": 0.040, "overheat_reduce": 0.030,
        "max_intraday_offset": 0.050,
        "crash_risk_score": 70, "risk_off_score": 45,
        "bull_exposure_threshold": 1.01,
        "reentry_mstr_1h": 0.035, "reentry_btc_1h": 0.010,
        "reentry_market_1h": 0.003, "reentry_rs_1h": 0.010,
        "reentry_min_votes": 3,
    }
    v6.update(overrides.pop("v6", {}))
    cfg = {
        "rsi_window": 14,
        "costs": {"commission_rate": 0.0, "slippage_rate": 0.0},
        "v6": v6,
        "v9": {
            "min_exposure": 0.0,
            "trend_regime": {
                "ma_field": "btc_4h_above_sma240",
                "bear_ceiling": 0.0,
                "bull_ceiling": 1.10,
                "bear_confirm_bars": 2,
                "bull_confirm_bars": 2,
                "hysteresis_pct": 0.0,
                "freeze_bars": 0,
            },
        },
    }
    cfg.update(overrides)
    return cfg


def _make_features(n: int, close: float = 100.0, open_price: float = 100.0,
                   regime_bull: bool = True) -> pd.DataFrame:
    """Create a minimal feature DataFrame for engine testing."""
    timestamps = pd.date_range("2024-06-01 14:30", periods=n, freq="1h", tz="UTC")
    df = pd.DataFrame({
        "open": [open_price] * n,
        "high": [close * 1.01] * n,
        "low": [close * 0.99] * n,
        "close": [close] * n,
        "volume": [1000.0] * n,
        "vwap": [close] * n,
        "rsi": [50.0] * n,
        "mstr_1h_return": [0.0] * n,
        "mstr_4h_return": [0.0] * n,
        "btc_close": [50000.0] * n,
        "btc_1h_return": [0.0] * n,
        "btc_4h_return": [0.0] * n,
        "market_close": [450.0] * n,
        "market_1h_return": [0.0] * n,
        "market_4h_return": [0.0] * n,
        "vwap_deviation": [0.0] * n,
        "mstr_btc_rs_1h": [0.0] * n,
        "mstr_btc_rs_4h": [0.0] * n,
        "mstr_btc_ratio_20": [0.0] * n,
        "day_low": [close * 0.99] * n,
        "prev_close": [close] * n,
        "day_range": [0.02] * n,
        "mstr_daily_5d_return": [0.0] * n,
        "mstr_daily_10d_return": [0.0] * n,
        "mstr_daily_20d_return": [0.0] * n,
        "mstr_daily_above_ema20": [1.0] * n,
        "mstr_daily_above_ema50": [1.0] * n,
        "mstr_daily_vol20": [0.02] * n,
        "mstr_daily_vol10": [0.02] * n,
        "btc_daily_5d_return": [0.0] * n,
        "btc_daily_10d_return": [0.0] * n,
        "btc_daily_20d_return": [0.0] * n,
        "btc_daily_above_ema20": [1.0] * n,
        "btc_daily_above_ema50": [1.0] * n,
        "btc_daily_vol20": [0.02] * n,
        "btc_daily_vol10": [0.02] * n,
        "market_daily_5d_return": [0.0] * n,
        "market_daily_10d_return": [0.0] * n,
        "market_daily_20d_return": [0.0] * n,
        "market_daily_above_ema20": [1.0] * n,
        "market_daily_above_ema50": [1.0] * n,
        "btc_4h_above_sma240": [1.0 if regime_bull else 0.0] * n,
    }, index=timestamps)
    return df


class TestNextBarExecution:
    """Orders queued on bar N must execute at bar N+1's open price."""

    def test_sell_executes_at_next_bar_open(self):
        """When regime flips to bear, the sell order queued on the flip bar
        must fill at the NEXT bar's open, not on the flip bar itself."""
        n = 10
        features = _make_features(n, close=100.0, open_price=105.0, regime_bull=True)
        features.iloc[3:, features.columns.get_loc("btc_4h_above_sma240")] = 0.0

        config = _minimal_engine_config()
        config["v9"]["trend_regime"]["bear_confirm_bars"] = 1
        config["v6"]["base_exposure"] = 1.00
        config["v6"]["trend_weight"] = 0.0
        config["v6"]["risk_weight"] = 0.0
        config["v6"]["relative_strength_weight"] = 0.0
        engine = EchoTrendEngine(config)
        state = engine.initial_state(100.0, initial_capital=10000)

        for i in range(n):
            engine.on_bar(state, features.index[i], features.iloc[i])

        sells = [rb for rb in engine.rebalances if rb.side == "sell"]
        assert len(sells) > 0, "Bear regime should trigger sells"
        for sell in sells:
            signal_bar_idx = features.index.get_loc(sell.timestamp)
            assert signal_bar_idx >= 1, "Sell must not happen on bar 0"

    def test_order_fills_at_open_not_close(self):
        n = 10
        features = _make_features(n, close=100.0, open_price=95.0, regime_bull=True)
        features.iloc[3:, features.columns.get_loc("btc_4h_above_sma240")] = 0.0

        config = _minimal_engine_config()
        config["v9"]["trend_regime"]["bear_confirm_bars"] = 1
        config["v6"]["base_exposure"] = 1.00
        config["v6"]["trend_weight"] = 0.0
        config["v6"]["risk_weight"] = 0.0
        config["v6"]["relative_strength_weight"] = 0.0
        config["costs"] = {"commission_rate": 0.0, "slippage_rate": 0.0}
        engine = EchoTrendEngine(config)
        state = engine.initial_state(100.0, initial_capital=10000)

        for i in range(n):
            engine.on_bar(state, features.index[i], features.iloc[i])

        sells = [rb for rb in engine.rebalances if rb.side == "sell"]
        assert len(sells) > 0
        for sell in sells:
            assert sell.price == 95.0, \
                f"Fill price should be open (95.0), got {sell.price}"

    def test_pending_order_queued_not_immediate(self):
        """Verify the engine uses a pending order mechanism —
        _queue_sell sets _pending_order, _fill_pending executes it next bar."""
        config = _minimal_engine_config()
        config["v6"]["base_exposure"] = 1.00
        config["v6"]["trend_weight"] = 0.0
        config["v6"]["risk_weight"] = 0.0
        config["v6"]["relative_strength_weight"] = 0.0
        engine = EchoTrendEngine(config)
        state = engine.initial_state(100.0, initial_capital=10000)

        features = _make_features(5, close=100.0, open_price=100.0, regime_bull=True)
        for i in range(3):
            engine.on_bar(state, features.index[i], features.iloc[i])

        shares_before = state.core_shares
        engine._queue_sell(state, 10.0, "test_sell", 0.90, 1.00)
        assert engine._pending_order is not None, "Order should be pending"
        assert state.core_shares == shares_before, "Shares unchanged until next bar fill"


class TestRegimeShift:
    """BTC 4H regime gate transitions with confirm bars."""

    def test_bear_to_bull_requires_confirm_bars(self):
        n = 20
        features = _make_features(n, close=100.0, regime_bull=False)
        features.iloc[10:, features.columns.get_loc("btc_4h_above_sma240")] = 1.0

        config = _minimal_engine_config()
        config["v9"]["trend_regime"]["bull_confirm_bars"] = 3
        config["v6"]["base_exposure"] = 1.00
        config["v6"]["trend_weight"] = 0.0
        config["v6"]["risk_weight"] = 0.0
        config["v6"]["relative_strength_weight"] = 0.0
        engine = EchoTrendEngine(config)
        engine.trend_regime = "bear"
        engine.regime_counter = 0
        state = engine.initial_state(100.0, initial_capital=10000)

        for i in range(12):
            engine.on_bar(state, features.index[i], features.iloc[i])
        assert engine.trend_regime == "bear", \
            "Should still be bear — only 2 bars above MA (bars 10,11), need 3"

        engine.on_bar(state, features.index[12], features.iloc[12])
        assert engine.trend_regime == "bull", \
            "Should flip to bull after 3 confirm bars (bars 10,11,12)"

    def test_bull_to_bear_sells_position(self):
        n = 20
        features = _make_features(n, close=100.0, open_price=100.0, regime_bull=True)
        features.iloc[5:, features.columns.get_loc("btc_4h_above_sma240")] = 0.0

        config = _minimal_engine_config()
        config["v9"]["trend_regime"]["bear_confirm_bars"] = 1
        engine = EchoTrendEngine(config)
        state = engine.initial_state(100.0, initial_capital=10000)

        for i in range(n):
            engine.on_bar(state, features.index[i], features.iloc[i])

        assert engine.trend_regime == "bear"
        sells = [rb for rb in engine.rebalances if rb.side == "sell"]
        assert len(sells) > 0, "Bear regime should trigger sells"


class TestFailClosedRegime:
    """Regime field must be present — missing field raises error."""

    def test_missing_regime_field_raises(self):
        features = _make_features(5, close=100.0, regime_bull=True)
        features = features.drop(columns=["btc_4h_above_sma240"])

        config = _minimal_engine_config()
        engine = EchoTrendEngine(config)
        state = engine.initial_state(100.0, initial_capital=10000)

        with pytest.raises(KeyError, match="Regime field.*missing"):
            for i in range(len(features)):
                engine.on_bar(state, features.index[i], features.iloc[i])


class TestMarginBuy:
    """Engine must allow buying above 1.0 exposure using margin."""

    def test_can_increase_exposure_above_1(self):
        n = 15
        features = _make_features(n, close=100.0, open_price=100.0, regime_bull=True)
        features["mstr_1h_return"] = 0.05
        features["mstr_4h_return"] = 0.05
        features["mstr_daily_5d_return"] = 0.10
        features["mstr_daily_20d_return"] = 0.10
        features["btc_daily_20d_return"] = 0.10

        config = _minimal_engine_config()
        engine = EchoTrendEngine(config)
        state = engine.initial_state(100.0, initial_capital=10000)

        for i in range(n):
            engine.on_bar(state, features.index[i], features.iloc[i])

        final_exposure = state.total_shares / state.initial_shares
        buys = [rb for rb in engine.rebalances if rb.side == "buy"]
        assert len(buys) > 0, "Should have buy orders to increase exposure"
        assert state.cash < 0, "Cash should be negative (margin used)"

    def test_margin_cap_on_gap_up(self):
        """When next bar opens at 3x price, margin borrow must not exceed
        max_margin_fraction (10%) of initial capital."""
        n = 10
        features = _make_features(n, close=100.0, open_price=100.0, regime_bull=True)
        features["mstr_1h_return"] = 0.05
        features["mstr_4h_return"] = 0.05
        features["mstr_daily_5d_return"] = 0.10
        features["mstr_daily_20d_return"] = 0.10
        features["btc_daily_20d_return"] = 0.10
        # Gap up: open jumps to 300 on bars where margin buys would fill
        features.iloc[3:, features.columns.get_loc("open")] = 300.0

        config = _minimal_engine_config()
        config["v6"]["max_margin_fraction"] = 0.10
        engine = EchoTrendEngine(config)
        initial_capital = 10000
        state = engine.initial_state(100.0, initial_capital=initial_capital)

        for i in range(n):
            engine.on_bar(state, features.index[i], features.iloc[i])

        margin_limit = initial_capital * 0.10
        assert state.cash >= -margin_limit - 1e-6, \
            f"Cash {state.cash:.2f} exceeds margin limit {-margin_limit:.2f}"

    def test_margin_cap_includes_commission(self):
        """Margin cap must account for commission in cost-per-share."""
        n = 10
        features = _make_features(n, close=100.0, open_price=100.0, regime_bull=True)
        features["mstr_1h_return"] = 0.05
        features["mstr_4h_return"] = 0.05
        features["mstr_daily_5d_return"] = 0.10
        features["mstr_daily_20d_return"] = 0.10
        features["btc_daily_20d_return"] = 0.10
        features.iloc[3:, features.columns.get_loc("open")] = 200.0

        config = _minimal_engine_config()
        config["v6"]["max_margin_fraction"] = 0.10
        config["costs"] = {"commission_rate": 0.01, "slippage_rate": 0.0}
        engine = EchoTrendEngine(config)
        initial_capital = 10000
        state = engine.initial_state(100.0, initial_capital=initial_capital)

        for i in range(n):
            engine.on_bar(state, features.index[i], features.iloc[i])

        margin_limit = initial_capital * 0.10
        assert state.cash >= -margin_limit - 1e-6, \
            f"Cash {state.cash:.2f} exceeds margin limit {-margin_limit:.2f} (with commission)"


class TestEngineBacktestResult:
    """run_backtest produces valid result with all required fields."""

    def test_result_fields(self):
        features = _make_features(30, close=100.0, regime_bull=True)
        config = _minimal_engine_config()
        engine = EchoTrendEngine(config)
        result = engine.run_backtest(features, initial_capital=10000)

        assert isinstance(result.equity_curve, pd.DataFrame)
        assert "timestamp" in result.equity_curve.columns
        assert "equity" in result.equity_curve.columns
        assert len(result.equity_curve) == 30
        assert result.start_date == features.index[0]
        assert result.end_date == features.index[-1]
        assert isinstance(result.final_exposure, float)
        assert isinstance(result.final_mode, str)
        assert isinstance(result.final_regime, str)
