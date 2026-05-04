"""Tests for EchoTrend 240 strategy signal logic."""
from __future__ import annotations

import pandas as pd
import numpy as np
import pytest

from strategies.echotrend_240.signal import (
    StrategyConfig,
    Signal,
    compute_signals,
    get_current_signal,
    get_filtered_df,
)
from strategies.echotrend_240.indicators import (
    filter_regular_hours,
    MARKET_TZ,
)


def make_btc_1h(prices: list[float], start: str = "2024-01-01") -> pd.DataFrame:
    """Create BTC 1H DataFrame from close prices."""
    timestamps = pd.date_range(start, periods=len(prices), freq="1h", tz="UTC")
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": prices,
        "high": [p * 1.01 for p in prices],
        "low": [p * 0.99 for p in prices],
        "close": prices,
        "volume": [100.0] * len(prices),
    })


def make_mstr_rth(prices: list[float], start: str = "2024-01-02") -> pd.DataFrame:
    """Create MSTR 1H DataFrame within NYSE regular hours (09:30-15:30 ET).

    Generates bars at 09:30, 10:30, ..., 15:30 ET (7 bars/day).
    """
    n = len(prices)
    timestamps = []
    base_date = pd.Timestamp(start, tz=MARKET_TZ)
    day_offset = 0
    bar_in_day = 0
    hours_per_day = [9, 10, 11, 12, 13, 14, 15]
    minutes = 30

    for _ in range(n):
        h = hours_per_day[bar_in_day]
        ts = (base_date + pd.Timedelta(days=day_offset)).replace(hour=h, minute=minutes)
        timestamps.append(ts.tz_convert("UTC"))
        bar_in_day += 1
        if bar_in_day >= len(hours_per_day):
            bar_in_day = 0
            day_offset += 1

    return pd.DataFrame({
        "timestamp": timestamps,
        "open": prices,
        "high": [p * 1.01 for p in prices],
        "low": [p * 0.99 for p in prices],
        "close": prices,
        "volume": [100.0] * n,
    })


def make_mstr_mixed_hours(n: int = 24) -> pd.DataFrame:
    """Create MSTR 1H data spanning full 24h UTC for filter testing."""
    timestamps = pd.date_range("2024-07-15", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": range(n),
        "high": range(n),
        "low": range(n),
        "close": range(n),
        "volume": [1.0] * n,
    })


def make_daily(prices: list[float], start: str = "2023-06-01") -> pd.DataFrame:
    """Create daily OHLCV DataFrame."""
    timestamps = pd.date_range(start, periods=len(prices), freq="1D", tz="UTC")
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": prices,
        "high": [p * 1.01 for p in prices],
        "low": [p * 0.99 for p in prices],
        "close": prices,
        "volume": [10000.0] * len(prices),
    })


def make_qqq_1h(prices: list[float], start: str = "2024-01-01") -> pd.DataFrame:
    """Create QQQ 1H DataFrame."""
    timestamps = pd.date_range(start, periods=len(prices), freq="1h", tz="UTC")
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": prices,
        "high": [p * 1.005 for p in prices],
        "low": [p * 0.995 for p in prices],
        "close": prices,
        "volume": [500.0] * len(prices),
    })


def _full_extra_data(n_1h: int = 300, n_daily: int = 100) -> dict:
    """Build a complete extra_data dict with all required sources."""
    return {
        "btc": make_btc_1h([50000.0] * n_1h),
        "qqq": make_qqq_1h([450.0] * n_1h),
        "mstr_daily": make_daily([300.0] * n_daily),
        "btc_daily": make_daily([50000.0] * n_daily),
        "qqq_daily": make_daily([450.0] * n_daily),
    }


class TestFilterRegularHours:
    def test_filters_to_et_regular_session(self):
        df = make_mstr_mixed_hours(24)
        filtered = filter_regular_hours(df)
        for _, row in filtered.iterrows():
            ts = row["timestamp"]
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            et = ts.tz_convert(MARKET_TZ)
            t = et.time()
            assert t >= pd.Timestamp("09:30").time()
            assert t < pd.Timestamp("16:00").time()

    def test_dst_aware_summer(self):
        ts_summer = pd.date_range("2024-07-15 13:00", periods=4, freq="1h", tz="UTC")
        df = pd.DataFrame({
            "timestamp": ts_summer,
            "open": [1, 2, 3, 4],
            "close": [1, 2, 3, 4],
            "volume": [1.0] * 4,
        })
        filtered = filter_regular_hours(df)
        assert len(filtered) == 3

    def test_dst_aware_winter(self):
        ts_winter = pd.date_range("2024-01-15 14:00", periods=4, freq="1h", tz="UTC")
        df = pd.DataFrame({
            "timestamp": ts_winter,
            "open": [1, 2, 3, 4],
            "close": [1, 2, 3, 4],
            "volume": [1.0] * 4,
        })
        filtered = filter_regular_hours(df)
        assert len(filtered) == 3

    def test_empty_if_no_regular_hours(self):
        timestamps = [
            pd.Timestamp("2024-01-01 00:00", tz="UTC"),
            pd.Timestamp("2024-01-01 05:00", tz="UTC"),
            pd.Timestamp("2024-01-01 08:00", tz="UTC"),
        ]
        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": [1, 2, 3],
            "close": [1, 2, 3],
            "volume": [1.0, 1.0, 1.0],
        })
        filtered = filter_regular_hours(df)
        assert len(filtered) == 0

    def test_half_hour_timestamps_preserved(self):
        timestamps = [
            pd.Timestamp("2024-07-15 09:00", tz="UTC"),
            pd.Timestamp("2024-07-15 13:00", tz="UTC"),
            pd.Timestamp("2024-07-15 13:30", tz="UTC"),
            pd.Timestamp("2024-07-15 14:30", tz="UTC"),
            pd.Timestamp("2024-07-15 15:30", tz="UTC"),
            pd.Timestamp("2024-07-15 16:30", tz="UTC"),
            pd.Timestamp("2024-07-15 17:30", tz="UTC"),
            pd.Timestamp("2024-07-15 18:30", tz="UTC"),
            pd.Timestamp("2024-07-15 19:30", tz="UTC"),
            pd.Timestamp("2024-07-15 20:00", tz="UTC"),
            pd.Timestamp("2024-07-15 21:00", tz="UTC"),
        ]
        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": range(len(timestamps)),
            "close": range(len(timestamps)),
            "volume": [1.0] * len(timestamps),
        })
        filtered = filter_regular_hours(df)
        assert len(filtered) == 7
        et_times = filtered["timestamp"].dt.tz_convert(MARKET_TZ).dt.time
        assert pd.Timestamp("09:30").time() in list(et_times)
        assert pd.Timestamp("15:30").time() in list(et_times)

    def test_winter_half_hour_timestamps(self):
        timestamps = [
            pd.Timestamp("2024-01-15 14:00", tz="UTC"),
            pd.Timestamp("2024-01-15 14:30", tz="UTC"),
            pd.Timestamp("2024-01-15 15:30", tz="UTC"),
            pd.Timestamp("2024-01-15 16:30", tz="UTC"),
            pd.Timestamp("2024-01-15 20:30", tz="UTC"),
            pd.Timestamp("2024-01-15 21:00", tz="UTC"),
        ]
        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": range(len(timestamps)),
            "close": range(len(timestamps)),
            "volume": [1.0] * len(timestamps),
        })
        filtered = filter_regular_hours(df)
        assert len(filtered) == 4
        et_times = filtered["timestamp"].dt.tz_convert(MARKET_TZ).dt.time
        assert pd.Timestamp("09:30").time() in list(et_times)


class TestComputeSignals:
    def test_returns_list(self):
        config = StrategyConfig()
        mstr_df = make_mstr_rth([300.0] * 10)
        signals = compute_signals(mstr_df, config, extra_data=_full_extra_data())
        assert isinstance(signals, list)

    def test_requires_btc_df(self):
        config = StrategyConfig()
        mstr_df = make_mstr_rth([300.0] * 10)
        with pytest.raises(ValueError, match="btc_df is required"):
            compute_signals(mstr_df, config, extra_data={})

    def test_requires_qqq_df(self):
        config = StrategyConfig()
        mstr_df = make_mstr_rth([300.0] * 10)
        with pytest.raises(ValueError, match="qqq_df is required"):
            compute_signals(mstr_df, config, extra_data={"btc": make_btc_1h([50000.0] * 10)})

    def test_signals_have_valid_actions(self):
        config = StrategyConfig(
            ma_window=5, bull_confirm_bars=1, bear_confirm_bars=1,
        )
        warmup_1h = 5 * 4 + 40
        base = 50000.0
        btc_prices = [base] * warmup_1h
        btc_prices.extend([base * 1.5] * 30)
        btc_prices.extend([base * 0.5] * 30)
        btc_prices.extend([base * 1.5] * 30)

        n_1h = len(btc_prices)
        mstr_count = 300

        extra = _full_extra_data(n_1h=n_1h)
        extra["btc"] = make_btc_1h(btc_prices)
        mstr_df = make_mstr_rth([300.0] * mstr_count)

        signals = compute_signals(mstr_df, config, extra_data=extra)
        for sig in signals:
            assert sig.action in ("buy", "sell")


class TestGetCurrentSignal:
    def test_requires_btc_df(self):
        config = StrategyConfig()
        mstr_df = make_mstr_rth([300.0] * 100)
        with pytest.raises(ValueError, match="btc_df is required"):
            get_current_signal(mstr_df, in_position=False, config=config, extra_data={})

    def test_hold_when_no_position_no_flip(self):
        config = StrategyConfig(ma_window=5, bull_confirm_bars=1, bear_confirm_bars=1)
        warmup_1h = 5 * 4 + 40
        base = 50000.0
        btc_prices = [base] * warmup_1h
        btc_prices.extend([base * 0.5] * 20)

        n_1h = len(btc_prices)
        extra = _full_extra_data(n_1h=n_1h)
        extra["btc"] = make_btc_1h(btc_prices)
        mstr_df = make_mstr_rth([300.0] * 200)

        signal = get_current_signal(mstr_df, in_position=False, config=config, extra_data=extra)
        assert signal.action == "hold"


class TestGetFilteredDf:
    def test_returns_only_regular_hours(self):
        df = make_mstr_mixed_hours(48)
        config = StrategyConfig(regular_hours_only=True)
        filtered = get_filtered_df(df, config)
        for _, row in filtered.iterrows():
            ts = row["timestamp"]
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            et = ts.tz_convert(MARKET_TZ)
            t = et.time()
            assert t >= pd.Timestamp("09:30").time()
            assert t < pd.Timestamp("16:00").time()

    def test_passthrough_when_disabled(self):
        df = make_mstr_mixed_hours(24)
        config = StrategyConfig(regular_hours_only=False)
        result = get_filtered_df(df, config)
        assert len(result) == 24
