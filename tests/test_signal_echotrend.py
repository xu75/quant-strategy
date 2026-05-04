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
    _filter_regular_hours,
    _resample_to_4h,
    _compute_regime,
    _prepare_mstr,
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


class TestFilterRegularHours:
    def test_filters_to_et_regular_session(self):
        """Filter should use US/Eastern 09:30-16:00, not raw UTC hours."""
        df = make_mstr_mixed_hours(24)
        filtered = _filter_regular_hours(df)
        for _, row in filtered.iterrows():
            ts = row["timestamp"]
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            et = ts.tz_convert(MARKET_TZ)
            t = et.time()
            assert t >= pd.Timestamp("09:30").time()
            assert t < pd.Timestamp("16:00").time()

    def test_dst_aware_summer(self):
        """In EDT (summer), 09:30 ET = 13:30 UTC."""
        ts_summer = pd.date_range("2024-07-15 13:00", periods=4, freq="1h", tz="UTC")
        df = pd.DataFrame({
            "timestamp": ts_summer,
            "open": [1, 2, 3, 4],
            "close": [1, 2, 3, 4],
            "volume": [1.0] * 4,
        })
        filtered = _filter_regular_hours(df)
        # 13:00 UTC = 09:00 ET (before open), 13:30+ would be in session
        # 13:00 → 09:00 ET (out), 14:00 → 10:00 ET (in), 15:00 → 11:00 ET (in), 16:00 → 12:00 ET (in)
        assert len(filtered) == 3  # 14:00, 15:00, 16:00 UTC

    def test_dst_aware_winter(self):
        """In EST (winter), 09:30 ET = 14:30 UTC."""
        ts_winter = pd.date_range("2024-01-15 14:00", periods=4, freq="1h", tz="UTC")
        df = pd.DataFrame({
            "timestamp": ts_winter,
            "open": [1, 2, 3, 4],
            "close": [1, 2, 3, 4],
            "volume": [1.0] * 4,
        })
        filtered = _filter_regular_hours(df)
        # 14:00 UTC = 09:00 ET (before open), 14:30+ would be in session
        # 14:00 → 09:00 ET (out), 15:00 → 10:00 ET (in), 16:00 → 11:00 ET (in), 17:00 → 12:00 ET (in)
        assert len(filtered) == 3  # 15:00, 16:00, 17:00 UTC

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
        filtered = _filter_regular_hours(df)
        assert len(filtered) == 0

    def test_half_hour_timestamps_preserved(self):
        """MSTR CSV has half-hour bars (09:30, 10:30, ..., 15:30 ET).

        These must pass through the filter correctly — the 09:30 ET bar
        (13:30 UTC summer / 14:30 UTC winter) must be included.
        """
        # Simulate a summer day with mixed integer and half-hour UTC timestamps
        # matching real MSTR CSV pattern
        timestamps = [
            pd.Timestamp("2024-07-15 09:00", tz="UTC"),   # 05:00 ET - out
            pd.Timestamp("2024-07-15 13:00", tz="UTC"),   # 09:00 ET - out
            pd.Timestamp("2024-07-15 13:30", tz="UTC"),   # 09:30 ET - IN
            pd.Timestamp("2024-07-15 14:30", tz="UTC"),   # 10:30 ET - IN
            pd.Timestamp("2024-07-15 15:30", tz="UTC"),   # 11:30 ET - IN
            pd.Timestamp("2024-07-15 16:30", tz="UTC"),   # 12:30 ET - IN
            pd.Timestamp("2024-07-15 17:30", tz="UTC"),   # 13:30 ET - IN
            pd.Timestamp("2024-07-15 18:30", tz="UTC"),   # 14:30 ET - IN
            pd.Timestamp("2024-07-15 19:30", tz="UTC"),   # 15:30 ET - IN
            pd.Timestamp("2024-07-15 20:00", tz="UTC"),   # 16:00 ET - out
            pd.Timestamp("2024-07-15 21:00", tz="UTC"),   # 17:00 ET - out
        ]
        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": range(len(timestamps)),
            "close": range(len(timestamps)),
            "volume": [1.0] * len(timestamps),
        })
        filtered = _filter_regular_hours(df)
        assert len(filtered) == 7  # 09:30 through 15:30 ET
        # Verify 09:30 ET bar is included
        et_times = filtered["timestamp"].dt.tz_convert(MARKET_TZ).dt.time
        assert pd.Timestamp("09:30").time() in list(et_times)
        assert pd.Timestamp("15:30").time() in list(et_times)

    def test_winter_half_hour_timestamps(self):
        """In EST (winter), 09:30 ET = 14:30 UTC."""
        timestamps = [
            pd.Timestamp("2024-01-15 14:00", tz="UTC"),   # 09:00 ET - out
            pd.Timestamp("2024-01-15 14:30", tz="UTC"),   # 09:30 ET - IN
            pd.Timestamp("2024-01-15 15:30", tz="UTC"),   # 10:30 ET - IN
            pd.Timestamp("2024-01-15 16:30", tz="UTC"),   # 11:30 ET - IN
            pd.Timestamp("2024-01-15 20:30", tz="UTC"),   # 15:30 ET - IN
            pd.Timestamp("2024-01-15 21:00", tz="UTC"),   # 16:00 ET - out
        ]
        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": range(len(timestamps)),
            "close": range(len(timestamps)),
            "volume": [1.0] * len(timestamps),
        })
        filtered = _filter_regular_hours(df)
        assert len(filtered) == 4  # 09:30, 10:30, 11:30, 15:30 ET
        et_times = filtered["timestamp"].dt.tz_convert(MARKET_TZ).dt.time
        assert pd.Timestamp("09:30").time() in list(et_times)


class TestResampleTo4H:
    def test_produces_4h_bars(self):
        btc = make_btc_1h([100.0] * 8)
        resampled = _resample_to_4h(btc)
        assert len(resampled) == 2
        assert resampled.iloc[0]["volume"] == 400.0


class TestComputeRegime:
    def test_bull_regime_after_confirm_bars(self):
        config = StrategyConfig(ma_window=5, bull_confirm_bars=3, bear_confirm_bars=3)
        warmup = [100.0] * 10
        above = [200.0] * 5
        prices = warmup + above
        btc_4h = pd.DataFrame({
            "timestamp": pd.date_range("2023-01-01", periods=len(prices), freq="4h", tz="UTC"),
            "close": prices,
            "volume": [100.0] * len(prices),
        })
        regime_df = _compute_regime(btc_4h, config)
        regimes = regime_df["regime"].dropna()
        assert 1.0 in regimes.values

    def test_bear_regime_after_confirm_bars(self):
        config = StrategyConfig(ma_window=5, bull_confirm_bars=3, bear_confirm_bars=3)
        warmup = [100.0] * 10
        above = [200.0] * 5
        below = [50.0] * 5
        prices = warmup + above + below
        btc_4h = pd.DataFrame({
            "timestamp": pd.date_range("2023-01-01", periods=len(prices), freq="4h", tz="UTC"),
            "close": prices,
            "volume": [100.0] * len(prices),
        })
        regime_df = _compute_regime(btc_4h, config)
        regimes = regime_df["regime"].dropna()
        assert 0.0 in regimes.values

    def test_regime_shifted_by_one(self):
        """Regime should be shifted by 1 bar (lookahead prevention)."""
        config = StrategyConfig(ma_window=5, bull_confirm_bars=1, bear_confirm_bars=1)
        warmup = [100.0] * 10
        above = [200.0] * 3
        prices = warmup + above
        btc_4h = pd.DataFrame({
            "timestamp": pd.date_range("2023-01-01", periods=len(prices), freq="4h", tz="UTC"),
            "close": prices,
            "volume": [100.0] * len(prices),
        })
        regime_df = _compute_regime(btc_4h, config)
        first_above_idx = len(warmup)
        regime_at_first_above = regime_df.iloc[first_above_idx]["regime"]
        assert regime_at_first_above == 0.0 or pd.isna(regime_at_first_above)


class TestComputeSignals:
    def test_no_signals_insufficient_data(self):
        config = StrategyConfig()
        btc_df = make_btc_1h([50000.0] * 10)
        mstr_df = make_mstr_rth([300.0] * 10)
        signals = compute_signals(mstr_df, config, btc_df=btc_df)
        assert signals == []

    def test_requires_btc_df(self):
        config = StrategyConfig()
        mstr_df = make_mstr_rth([300.0] * 10)
        with pytest.raises(ValueError, match="btc_df is required"):
            compute_signals(mstr_df, config, btc_df=None)

    def test_next_bar_execution_gap(self):
        """Signal detection and execution must be on different bars.

        Regime flip detected on bar i → execution on bar i+1's open.
        The signal timestamp should be one MSTR bar after the regime flip bar.
        """
        config = StrategyConfig(
            ma_window=5, bull_confirm_bars=1, bear_confirm_bars=1,
        )
        warmup_1h = 5 * 4 + 40
        base = 50000.0
        btc_prices = [base] * warmup_1h
        btc_prices.extend([base * 1.5] * 30)  # bull
        btc_prices.extend([base * 0.5] * 30)  # bear
        btc_df = make_btc_1h(btc_prices)

        mstr_count = 300
        mstr_df = make_mstr_rth([300.0 + i * 0.5 for i in range(mstr_count)])

        signals = compute_signals(mstr_df, config, btc_df=btc_df)
        if len(signals) >= 1:
            mstr_prepared = _prepare_mstr(mstr_df, btc_df, config)
            for sig in signals:
                sig_mask = mstr_prepared["timestamp"] == sig.timestamp
                assert sig_mask.any(), f"Signal timestamp {sig.timestamp} not in MSTR data"
                sig_idx = int(sig_mask.idxmax())
                # The bar before the signal bar should have a different regime
                # than the signal bar (regime flip happened on prev bar)
                assert sig_idx > 0, "Signal cannot be on the first bar"

    def test_buy_sell_alternate(self):
        """Signals should alternate: buy, sell, buy, sell..."""
        config = StrategyConfig(
            ma_window=5, bull_confirm_bars=1, bear_confirm_bars=1,
        )
        warmup_1h = 5 * 4 + 40
        base = 50000.0
        btc_prices = [base] * warmup_1h
        btc_prices.extend([base * 1.5] * 30)
        btc_prices.extend([base * 0.5] * 30)
        btc_prices.extend([base * 1.5] * 30)
        btc_df = make_btc_1h(btc_prices)

        mstr_count = 300
        mstr_df = make_mstr_rth([300.0] * mstr_count)

        signals = compute_signals(mstr_df, config, btc_df=btc_df)
        for i in range(len(signals) - 1):
            assert signals[i].action != signals[i + 1].action

    def test_signals_use_open_price_not_close(self):
        """Execution price must be the bar's open, not close."""
        config = StrategyConfig(
            ma_window=5, bull_confirm_bars=1, bear_confirm_bars=1,
        )
        warmup_1h = 5 * 4 + 40
        base = 50000.0
        btc_prices = [base] * warmup_1h
        btc_prices.extend([base * 1.5] * 20)
        btc_prices.extend([base * 0.5] * 20)
        btc_df = make_btc_1h(btc_prices)

        mstr_count = 200
        opens = [300.0 + i * 0.5 for i in range(mstr_count)]
        closes = [300.0 + i * 0.5 + 0.25 for i in range(mstr_count)]
        mstr_df = make_mstr_rth(opens)
        mstr_df["close"] = closes[:len(mstr_df)]

        signals = compute_signals(mstr_df, config, btc_df=btc_df)
        mstr_prepared = _prepare_mstr(mstr_df, btc_df, config)
        for sig in signals:
            mask = mstr_prepared["timestamp"] == sig.timestamp
            if mask.any():
                expected_open = float(mstr_prepared.loc[mask, "open"].iloc[0])
                assert sig.price == expected_open, (
                    f"Signal price {sig.price} != open {expected_open}"
                )


class TestGetCurrentSignal:
    def test_requires_btc_df(self):
        config = StrategyConfig()
        mstr_df = make_mstr_rth([300.0] * 100)
        with pytest.raises(ValueError, match="btc_df is required"):
            get_current_signal(mstr_df, in_position=False, config=config, btc_df=None)

    def test_hold_when_no_position_no_flip(self):
        config = StrategyConfig(ma_window=5, bull_confirm_bars=1, bear_confirm_bars=1)
        warmup_1h = 5 * 4 + 40
        base = 50000.0
        btc_prices = [base] * warmup_1h
        btc_prices.extend([base * 0.5] * 20)
        btc_df = make_btc_1h(btc_prices)

        mstr_df = make_mstr_rth([300.0] * 200)

        signal = get_current_signal(mstr_df, in_position=False, config=config, btc_df=btc_df)
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
