"""V3 exposure data-contract tests.

The daily pipeline collapses intraday buy/sell events into the last one, so
`action` alone is misleading. The contract that the UI + notifier rely on:

  - current_exposure : executed model position (what the user should hold now)
  - target_exposure  : the TRUE strategy target (bear=0.0, bull=1.0, CB=0.0)
  - exposure_state   : reducing / increasing / holding (current vs target)

`target_exposure` must NOT be the executed exposure (the original bug).
"""
from __future__ import annotations

import pandas as pd

from strategies.echotrend_240_v3.engine import EchoTrendV3Engine, PortfolioState
from strategies.echotrend_240_v3.signal import (
    _build_engine_config,
    StrategyConfig,
    exposure_state,
)


def _prod_config():
    return _build_engine_config(StrategyConfig())


def _row(**ov):
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
    row.update(ov)
    return pd.Series(row)


class TestExposureStateHelper:
    def test_reducing_when_target_below_current(self):
        # current 24%, target 0% -> reducing (the real MSTR case)
        assert exposure_state(0.2408, 0.0) == "reducing"

    def test_increasing_when_target_above_current(self):
        assert exposure_state(0.10, 1.0) == "increasing"

    def test_holding_when_close(self):
        assert exposure_state(0.50, 0.50) == "holding"


class TestEngineExposesTrueTarget:
    """After a confirmed bear flip, the engine's final target must be 0.0 even
    while executed exposure is still being wound down."""

    def test_bear_regime_target_is_zero(self):
        engine = EchoTrendV3Engine(_prod_config())
        state = PortfolioState(cash=0.0, shares=100.0, initial_capital=10000.0)

        dt0 = pd.Timestamp("2024-01-15 10:00:00", tz="US/Eastern")
        engine.on_bar(state, dt0, _row())  # prev_row seed (bull)

        # 3 consecutive MSTR 1H bars below BTC 4H SMA -> bear confirmed (bear_confirm=3)
        for i in range(1, 6):
            dt = dt0 + pd.Timedelta(hours=i)
            engine.on_bar(state, dt, _row(btc_4h_above_sma240=0.0))

        assert engine.trend_regime == "bear"
        # The exposed final target must be the bear ceiling (0.0), not executed exposure.
        assert engine.last_target_exposure == 0.0

    def test_bull_regime_target_is_one(self):
        engine = EchoTrendV3Engine(_prod_config())
        state = PortfolioState(cash=0.0, shares=100.0, initial_capital=10000.0)
        dt0 = pd.Timestamp("2024-01-15 10:00:00", tz="US/Eastern")
        for i in range(0, 4):
            dt = dt0 + pd.Timedelta(hours=i)
            engine.on_bar(state, dt, _row(btc_4h_above_sma240=1.0))
        assert engine.trend_regime == "bull"
        assert engine.last_target_exposure == 1.0


class TestBacktestResultCarriesTarget:
    def test_result_has_final_target_exposure(self):
        import numpy as np
        idx = pd.date_range("2024-01-15 10:00", periods=30, freq="1h", tz="US/Eastern")
        rows = [_row(btc_4h_above_sma240=(0.0 if k > 10 else 1.0)) for k in range(30)]
        feats = pd.DataFrame(rows, index=idx)
        engine = EchoTrendV3Engine(_prod_config())
        result = engine.run_backtest(feats, initial_capital=10000.0)
        # New field: the true strategy target at the final bar.
        assert hasattr(result, "final_target_exposure")
        assert result.final_target_exposure == 0.0  # ended in bear
