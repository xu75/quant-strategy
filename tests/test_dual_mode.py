"""Tests for dual-mode pipeline: state serialization, incremental resume, fail-closed guards.

Covers:
  1. State round-trip: export → import → fields match
  2. Breakpoint parity: full replay == partial replay from mid-state
  3. Pending order survives serialize/deserialize and fills on resume
  4. Fail-closed: missing state, config hash mismatch, version mismatch, watermark gap
  5. daily-signal mode does not write backtest.json or charts
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import numpy as np
import pytest

from strategies.echotrend_240.engine import EchoTrendEngine
from core.state import config_hash, validate_state_for_resume, load_state, save_state


def _minimal_engine_config(**overrides) -> dict:
    v6 = {
        "min_exposure": 0.70,
        "max_exposure": 1.10,
        "max_margin_fraction": 0.10,
        "base_exposure": 1.00,
        "trend_weight": 0.12,
        "risk_weight": 0.40,
        "relative_strength_weight": 0.07,
        "target_ema_alpha": 0.30,
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
                   regime_bull: bool = True, seed: int = 42) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    timestamps = pd.date_range("2024-06-01 14:30", periods=n, freq="1h", tz="UTC")
    closes = close + rng.randn(n).cumsum() * 0.5
    opens = closes + rng.randn(n) * 0.2
    df = pd.DataFrame({
        "open": opens,
        "high": closes * 1.01,
        "low": closes * 0.99,
        "close": closes,
        "volume": [1000.0] * n,
        "vwap": closes,
        "rsi": [50.0] * n,
        "mstr_1h_return": rng.randn(n) * 0.02,
        "mstr_4h_return": rng.randn(n) * 0.03,
        "btc_close": [50000.0] * n,
        "btc_1h_return": rng.randn(n) * 0.01,
        "btc_4h_return": rng.randn(n) * 0.01,
        "market_close": [450.0] * n,
        "market_1h_return": rng.randn(n) * 0.005,
        "market_4h_return": rng.randn(n) * 0.005,
        "vwap_deviation": [0.0] * n,
        "mstr_btc_rs_1h": rng.randn(n) * 0.01,
        "mstr_btc_rs_4h": rng.randn(n) * 0.02,
        "mstr_btc_ratio_20": [0.0] * n,
        "day_low": closes * 0.99,
        "prev_close": closes,
        "day_range": [0.02] * n,
        "mstr_daily_5d_return": rng.randn(n) * 0.05,
        "mstr_daily_10d_return": [0.0] * n,
        "mstr_daily_20d_return": rng.randn(n) * 0.08,
        "mstr_daily_above_ema20": [1.0] * n,
        "mstr_daily_above_ema50": [1.0] * n,
        "mstr_daily_vol20": [0.02] * n,
        "mstr_daily_vol10": [0.02] * n,
        "btc_daily_5d_return": [0.0] * n,
        "btc_daily_10d_return": [0.0] * n,
        "btc_daily_20d_return": rng.randn(n) * 0.05,
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


class TestStateRoundTrip:
    """export_state → JSON serialize → import_state preserves all fields."""

    def test_round_trip_fields(self):
        n = 30
        features = _make_features(n)
        config = _minimal_engine_config()
        engine = EchoTrendEngine(config)
        first_price = float(features.iloc[0]["close"])
        state = engine.initial_state(first_price, initial_capital=10000)

        for i in range(n):
            engine.on_bar(state, features.index[i], features.iloc[i])

        watermark = features.index[-1]
        snapshot = engine.export_state(state, watermark)

        # Simulate JSON round-trip
        json_str = json.dumps(snapshot, default=str)
        restored_snapshot = json.loads(json_str)

        engine2 = EchoTrendEngine(config)
        state2 = engine2.import_state(restored_snapshot)

        assert engine2.bar_index == engine.bar_index
        assert engine2.mode == engine.mode
        assert engine2.mode_counter == engine.mode_counter
        assert engine2.trend_regime == engine.trend_regime
        assert engine2.regime_counter == engine.regime_counter
        assert engine2.regime_change_bar == engine.regime_change_bar
        assert engine2.regime_flips == engine.regime_flips
        assert engine2.last_rebalance_bar == engine.last_rebalance_bar
        assert abs(engine2.exposure_ema - engine.exposure_ema) < 1e-10
        assert state2.cash == pytest.approx(state.cash)
        assert state2.core_shares == pytest.approx(state.core_shares)
        assert state2.initial_shares == pytest.approx(state.initial_shares)
        assert state2.initial_capital == pytest.approx(state.initial_capital)
        assert state2.total_costs == pytest.approx(state.total_costs)


class TestBreakpointParity:
    """Full replay N bars == replay N/2 bars + export + import + replay remaining."""

    def test_parity_exposure_mode_regime(self):
        n = 60
        split = 30
        features = _make_features(n)
        config = _minimal_engine_config()

        # Full replay
        engine_full = EchoTrendEngine(config)
        first_price = float(features.iloc[0]["close"])
        state_full = engine_full.initial_state(first_price, initial_capital=10000)
        for i in range(n):
            engine_full.on_bar(state_full, features.index[i], features.iloc[i])

        # Split replay: first half
        engine_a = EchoTrendEngine(config)
        state_a = engine_a.initial_state(first_price, initial_capital=10000)
        for i in range(split):
            engine_a.on_bar(state_a, features.index[i], features.iloc[i])

        # Export + JSON round-trip + import
        snapshot = engine_a.export_state(state_a, features.index[split - 1])
        json_str = json.dumps(snapshot, default=str)
        restored = json.loads(json_str)

        engine_b = EchoTrendEngine(config)
        state_b = engine_b.import_state(restored)

        # Second half
        for i in range(split, n):
            engine_b.on_bar(state_b, features.index[i], features.iloc[i])

        # Compare final state
        exp_full = state_full.total_shares / state_full.initial_shares
        exp_split = state_b.total_shares / state_b.initial_shares
        assert exp_split == pytest.approx(exp_full, abs=1e-8), \
            f"Exposure mismatch: full={exp_full}, split={exp_split}"
        assert engine_b.mode == engine_full.mode
        assert engine_b.trend_regime == engine_full.trend_regime
        assert state_b.cash == pytest.approx(state_full.cash, abs=1e-6)
        assert state_b.core_shares == pytest.approx(state_full.core_shares, abs=1e-8)
        assert engine_b._pending_order == engine_full._pending_order


class TestPendingOrderResume:
    """Pending order serialized in state must fill on first bar after resume."""

    def test_pending_sell_fills_after_resume(self):
        n = 20
        features = _make_features(n, close=100.0, open_price=100.0)
        # Force a regime flip to trigger a sell
        features.iloc[8:, features.columns.get_loc("btc_4h_above_sma240")] = 0.0

        config = _minimal_engine_config()
        config["v9"]["trend_regime"]["bear_confirm_bars"] = 1
        engine = EchoTrendEngine(config)
        first_price = float(features.iloc[0]["close"])
        state = engine.initial_state(first_price, initial_capital=10000)

        # Run until a pending order exists
        split = None
        for i in range(n):
            engine.on_bar(state, features.index[i], features.iloc[i])
            if engine._pending_order is not None and split is None:
                split = i + 1
                break

        if split is None:
            pytest.skip("No pending order generated in test data")

        # Export with pending order
        snapshot = engine.export_state(state, features.index[split - 1])
        assert snapshot["engine"]["pending_order"] is not None

        # Import and continue
        engine2 = EchoTrendEngine(config)
        state2 = engine2.import_state(json.loads(json.dumps(snapshot, default=str)))
        assert engine2._pending_order is not None

        shares_before = state2.core_shares
        engine2.on_bar(state2, features.index[split], features.iloc[split])
        # Pending order should have been filled (shares changed)
        assert state2.core_shares != shares_before or engine2._pending_order is None, \
            "Pending order should fill or clear on first bar after resume"


class TestFailClosed:
    """validate_state_for_resume rejects invalid states."""

    def _base_state(self) -> dict:
        return {
            "strategy_id": "echotrend_240",
            "strategy_version": "2.0.0",
            "config_hash": "abc123",
            "watermark": "2024-06-02T10:30:00+00:00",
            "engine": {},
            "portfolio": {},
        }

    def test_missing_state(self):
        error = validate_state_for_resume(
            self._base_state(),
            strategy_id="wrong_id",
            strategy_version="2.0.0",
            current_config_hash="abc123",
            latest_data_ts=pd.Timestamp("2024-06-02T12:00:00", tz="UTC"),
            warmup_bars=960,
        )
        assert error is not None
        assert "strategy_id mismatch" in error

    def test_version_mismatch(self):
        error = validate_state_for_resume(
            self._base_state(),
            strategy_id="echotrend_240",
            strategy_version="3.0.0",
            current_config_hash="abc123",
            latest_data_ts=pd.Timestamp("2024-06-02T12:00:00", tz="UTC"),
            warmup_bars=960,
        )
        assert error is not None
        assert "strategy_version mismatch" in error

    def test_config_hash_mismatch(self):
        error = validate_state_for_resume(
            self._base_state(),
            strategy_id="echotrend_240",
            strategy_version="2.0.0",
            current_config_hash="different_hash",
            latest_data_ts=pd.Timestamp("2024-06-02T12:00:00", tz="UTC"),
            warmup_bars=960,
        )
        assert error is not None
        assert "config_hash mismatch" in error

    def test_watermark_gap_too_large(self):
        error = validate_state_for_resume(
            self._base_state(),
            strategy_id="echotrend_240",
            strategy_version="2.0.0",
            current_config_hash="abc123",
            latest_data_ts=pd.Timestamp("2024-09-01T12:00:00", tz="UTC"),
            warmup_bars=960,
            bar_interval_hours=1.0,
        )
        assert error is not None
        assert "watermark gap too large" in error

    def test_watermark_ahead_of_data(self):
        error = validate_state_for_resume(
            self._base_state(),
            strategy_id="echotrend_240",
            strategy_version="2.0.0",
            current_config_hash="abc123",
            latest_data_ts=pd.Timestamp("2024-06-01T12:00:00", tz="UTC"),
            warmup_bars=960,
        )
        assert error is not None
        assert "ahead of latest data" in error

    def test_valid_state_passes(self):
        error = validate_state_for_resume(
            self._base_state(),
            strategy_id="echotrend_240",
            strategy_version="2.0.0",
            current_config_hash="abc123",
            latest_data_ts=pd.Timestamp("2024-06-02T14:00:00", tz="UTC"),
            warmup_bars=960,
        )
        assert error is None


class TestConfigHash:
    """config_hash is deterministic and detects changes."""

    def test_deterministic(self):
        cfg = {"ma_window": 240, "bear_ceiling": 0.0}
        assert config_hash(cfg) == config_hash(cfg)

    def test_detects_change(self):
        cfg1 = {"ma_window": 240, "bear_ceiling": 0.0}
        cfg2 = {"ma_window": 240, "bear_ceiling": 0.1}
        assert config_hash(cfg1) != config_hash(cfg2)


class TestStatePersistence:
    """save_state / load_state round-trip via filesystem."""

    def test_save_load(self, tmp_path):
        state = {"strategy_id": "test", "engine": {"bar_index": 42}}
        path = tmp_path / "state.json"
        save_state(path, state)
        loaded = load_state(path)
        assert loaded == state

    def test_load_missing(self, tmp_path):
        assert load_state(tmp_path / "nonexistent.json") is None

    def test_load_corrupt(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json{{{")
        assert load_state(path) is None


class TestDailySignalContract:
    """Runner daily-signal mode must not fallback, must not call get_current_signal,
    must not write backtest.json or charts."""

    def test_no_fallback_for_unsupported_strategy(self, tmp_path, monkeypatch):
        """Strategies without run_incremental are skipped, not fallback to full-backtest."""
        from unittest.mock import MagicMock, patch
        from core.runner import _run_daily_signal

        adapter = MagicMock()
        adapter.manifest.id = "test_strat"
        adapter.manifest.name = "Test"
        adapter.run_incremental = None

        # Should not call _run_full_backtest
        with patch("core.runner._run_full_backtest") as mock_full:
            _run_daily_signal(adapter)
            mock_full.assert_not_called()

    def test_no_get_current_signal_call(self, tmp_path, monkeypatch):
        """daily-signal must use signal from run_incremental, not get_current_signal."""
        from unittest.mock import MagicMock, patch
        from core.runner import _run_daily_signal
        from core.state import save_state, config_hash

        adapter = MagicMock()
        adapter.manifest.id = "echotrend_240"
        adapter.manifest.name = "EchoTrend 240"
        adapter.manifest.version = "2.0.0"
        adapter.manifest.config = {"ma_window": 240}
        adapter.config = MagicMock(warmup_bars=960, timeframe="1H")
        adapter.get_filtered_df = None

        output_dir = tmp_path / "echotrend_240"
        output_dir.mkdir(parents=True)
        chash = config_hash({"ma_window": 240})
        save_state(output_dir / "state.json", {
            "strategy_id": "echotrend_240",
            "strategy_version": "2.0.0",
            "config_hash": chash,
            "watermark": "2024-06-01T20:30:00+00:00",
            "engine": {},
            "portfolio": {},
        })

        df = pd.DataFrame({
            "timestamp": pd.date_range("2024-06-01 14:30", periods=10, freq="1h", tz="UTC"),
            "close": [100.0] * 10,
        })

        signal_mock = MagicMock()
        adapter.run_incremental.return_value = {
            "watermark": pd.Timestamp("2024-06-01T23:30:00+00:00"),
            "exposure": 0.95,
            "mode": "neutral",
            "regime": "bull",
            "state": {"engine": {}, "portfolio": {}},
            "current_signal": signal_mock,
        }

        monkeypatch.setattr("core.runner.OUTPUT_BASE_DIR", tmp_path)

        with patch("core.runner.load_strategy_data", return_value=df), \
             patch("core.runner.load_extra_data_sources", return_value={}), \
             patch("core.runner.generate_status_json") as mock_gen:
            _run_daily_signal(adapter)
            adapter.get_current_signal.assert_not_called()
            mock_gen.assert_called_once()
            assert mock_gen.call_args.kwargs.get("current_signal") is signal_mock

    def test_no_backtest_or_charts_written(self, tmp_path, monkeypatch):
        """daily-signal must not write backtest.json or chart files."""
        from unittest.mock import MagicMock, patch
        from core.runner import _run_daily_signal
        from core.state import save_state, config_hash

        adapter = MagicMock()
        adapter.manifest.id = "echotrend_240"
        adapter.manifest.name = "EchoTrend 240"
        adapter.manifest.version = "2.0.0"
        adapter.manifest.config = {"ma_window": 240}
        adapter.config = MagicMock(warmup_bars=960, timeframe="1H")
        adapter.get_filtered_df = None

        output_dir = tmp_path / "echotrend_240"
        output_dir.mkdir(parents=True)
        chash = config_hash({"ma_window": 240})
        save_state(output_dir / "state.json", {
            "strategy_id": "echotrend_240",
            "strategy_version": "2.0.0",
            "config_hash": chash,
            "watermark": "2024-06-01T20:30:00+00:00",
            "engine": {},
            "portfolio": {},
        })

        df = pd.DataFrame({
            "timestamp": pd.date_range("2024-06-01 14:30", periods=10, freq="1h", tz="UTC"),
            "close": [100.0] * 10,
        })

        signal_mock = MagicMock()
        adapter.run_incremental.return_value = {
            "watermark": pd.Timestamp("2024-06-01T23:30:00+00:00"),
            "exposure": 0.95,
            "mode": "neutral",
            "regime": "bull",
            "state": {"engine": {}, "portfolio": {}},
            "current_signal": signal_mock,
        }

        monkeypatch.setattr("core.runner.OUTPUT_BASE_DIR", tmp_path)

        with patch("core.runner.load_strategy_data", return_value=df), \
             patch("core.runner.load_extra_data_sources", return_value={}), \
             patch("core.runner.generate_status_json"), \
             patch("core.runner.generate_backtest_json") as mock_bt, \
             patch("core.runner.generate_equity_chart") as mock_eq, \
             patch("core.runner.generate_price_ma_chart") as mock_pm:
            _run_daily_signal(adapter)
            mock_bt.assert_not_called()
            mock_eq.assert_not_called()
            mock_pm.assert_not_called()
