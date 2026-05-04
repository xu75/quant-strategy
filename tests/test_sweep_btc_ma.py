from __future__ import annotations

"""Tests for the BTC MA/freeze sweep research script."""

import pandas as pd
import pytest

from sweep_btc_ma import (
    build_regime,
    infer_bars_per_day,
    ma_period_for_interval,
    run_execution_against_signal_ma,
    run_signal_on_execution,
    run_trend_gate,
)


def make_ohlcv(closes: list[float], opens: list[float] | None = None, freq: str = "1h") -> pd.DataFrame:
    if opens is None:
        opens = closes
    index = pd.date_range("2024-01-01", periods=len(closes), freq=freq, tz="UTC")
    return pd.DataFrame(
        {
            "open": opens,
            "high": [max(o, c) for o, c in zip(opens, closes)],
            "low": [min(o, c) for o, c in zip(opens, closes)],
            "close": closes,
            "volume": [1.0] * len(closes),
        },
        index=index,
    )


def test_ma_period_converts_from_4h_ma240():
    assert ma_period_for_interval("1h") == 960
    assert ma_period_for_interval("4h") == 240
    assert ma_period_for_interval("1d") == 40


def test_infer_bars_per_day_uses_actual_data_density():
    hourly = make_ohlcv([100.0] * 72, freq="1h")
    four_hour = make_ohlcv([100.0] * 18, freq="4h")

    assert infer_bars_per_day(hourly, "1h") == 24
    assert infer_bars_per_day(four_hour, "4h") == 6


def test_build_regime_ignores_reversal_inside_freeze_window():
    raw = pd.Series([0, 1, 0, 0, 1])

    regime, flips = build_regime(raw, freeze_bars=2)

    assert regime.tolist() == [0, 1, 1, 0, 0]
    assert flips == 2


def test_trend_gate_executes_signal_on_next_bar_open():
    df = make_ohlcv(
        closes=[100, 100, 110, 200],
        opens=[100, 100, 110, 250],
        freq="1h",
    )

    result = run_trend_gate(df, ma_period=2, freeze_bars=0, fee_rate=0.0)

    assert result.trades == 1
    assert result.executions[0]["timestamp"] == df.index[3]
    assert result.executions[0]["price"] == 250
    assert result.total_return == pytest.approx(-0.2)


def test_long_timeframe_signal_executes_on_next_short_bar_open():
    exec_df = make_ohlcv(
        closes=[100, 100, 100, 100, 100, 100, 100, 100, 200],
        opens=[100, 100, 100, 100, 100, 100, 100, 100, 250],
        freq="1h",
    )
    signal_index = pd.date_range("2024-01-01", periods=2, freq="4h", tz="UTC")
    signal_df = pd.DataFrame(
        {
            "open": [100, 100],
            "high": [100, 110],
            "low": [90, 100],
            "close": [90, 110],
            "volume": [1.0, 1.0],
            "ma": [100, 100],
        },
        index=signal_index,
    )

    result = run_signal_on_execution(
        exec_df=exec_df,
        signal_df=signal_df,
        signal_interval="4h",
        signal_ma_period=2,
        freeze_bars=0,
        fee_rate=0.0,
    )

    assert result.trades == 1
    assert result.executions[0]["timestamp"] == exec_df.index[8]
    assert result.executions[0]["price"] == 250
    assert result.total_return == pytest.approx(-0.2)


def test_short_bar_trigger_can_use_long_timeframe_ma():
    exec_df = make_ohlcv(
        closes=[100, 100, 100, 100, 100, 100, 100, 100, 200, 200],
        opens=[100, 100, 100, 100, 100, 100, 100, 100, 200, 250],
        freq="1h",
    )
    signal_index = pd.date_range("2024-01-01", periods=2, freq="4h", tz="UTC")
    signal_df = pd.DataFrame(
        {
            "open": [100, 100],
            "high": [100, 110],
            "low": [90, 100],
            "close": [90, 110],
            "volume": [1.0, 1.0],
            "ma": [100, 100],
        },
        index=signal_index,
    )

    result = run_execution_against_signal_ma(
        exec_df=exec_df,
        signal_df=signal_df,
        signal_interval="4h",
        signal_ma_period=2,
        freeze_bars=0,
        fee_rate=0.0,
    )

    assert result.trades == 1
    assert result.executions[0]["timestamp"] == exec_df.index[9]
    assert result.executions[0]["price"] == 250
    assert result.total_return == pytest.approx(-0.2)
