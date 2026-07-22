"""Tests for Futu K_60M timestamp correction (close-labeled → bar-open)."""
from __future__ import annotations

import pandas as pd
import pytest

from pipeline.data_fetcher import _relabel_futu_hourly_rth_to_bar_open, _apply_futu_hourly_correction


def test_relabel_futu_hourly_rth_to_bar_open():
    """Test that Futu close-labeled hourly bars are correctly shifted to bar-open time."""
    # Futu K_60M exports RTH candles with their END time
    # 09:30 row is zero-volume snapshot, first completed 1H candle is labeled 10:30,
    # final half-hour candle is labeled 16:00
    data = {
        "open": [99, 100, 101, 105],
        "high": [99, 102, 103, 106],
        "low": [99, 99, 100, 104],
        "close": [99, 101, 102, 105],
        "volume": [0, 1000, 1000, 500],
    }
    # These are the timestamps as they appear in Futu data (already in US/Eastern)
    index = pd.DatetimeIndex([
        "2021-01-04 09:30:00",  # 09:30 ET snapshot (zero volume)
        "2021-01-04 10:30:00",  # 10:30 ET label (09:30-10:30 bar)
        "2021-01-04 11:30:00",  # 11:30 ET label (10:30-11:30 bar)
        "2021-01-04 16:00:00",  # 16:00 ET label (15:30-16:00 half-hour)
    ], tz="US/Eastern")

    df = pd.DataFrame(data, index=index)
    result = _relabel_futu_hourly_rth_to_bar_open(df)

    # Expected: bars shifted to their executable open time
    # 10:30 label → 09:30 (shift -1h), 11:30 → 10:30, 16:00 → 15:30 (shift -30min)
    expected_index = pd.DatetimeIndex([
        "2021-01-04 09:30:00",  # 10:30 → 09:30 (shift -1h)
        "2021-01-04 10:30:00",  # 11:30 → 10:30 (shift -1h)
        "2021-01-04 15:30:00",  # 16:00 → 15:30 (shift -30min)
    ], tz="US/Eastern")

    assert result.index.equals(expected_index), f"Expected {expected_index}, got {result.index}"
    assert result["open"].tolist() == [100, 101, 105]
    assert result["volume"].tolist() == [1000, 1000, 500]


def test_apply_futu_hourly_correction_with_timestamp_column():
    """Test the wrapper that handles timestamp column format."""
    df = pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2021-01-04T14:30:00Z",  # 09:30 ET snapshot
            "2021-01-04T15:30:00Z",  # 10:30 ET
            "2021-01-04T16:30:00Z",  # 11:30 ET
            "2021-01-04T21:00:00Z",  # 16:00 ET
        ], utc=True),
        "open": [99, 100, 101, 105],
        "high": [99, 102, 103, 106],
        "low": [99, 99, 100, 104],
        "close": [99, 101, 102, 105],
        "volume": [0, 1000, 1000, 500],
    })

    result = _apply_futu_hourly_correction(df)

    # Expected timestamps after correction (back in UTC)
    expected_timestamps = pd.to_datetime([
        "2021-01-04T14:30:00Z",  # 09:30 ET
        "2021-01-04T15:30:00Z",  # 10:30 ET
        "2021-01-04T20:30:00Z",  # 15:30 ET
    ], utc=True)

    assert len(result) == 3, f"Expected 3 rows, got {len(result)}"
    assert result["timestamp"].tolist() == expected_timestamps.tolist()
    assert result["open"].tolist() == [100, 101, 105]
    assert result["volume"].tolist() == [1000, 1000, 500]
    assert result["open"].tolist() == [100, 101, 105]
    assert result["volume"].tolist() == [1000, 1000, 500]


def test_relabel_empty_dataframe():
    """Test that empty DataFrame is handled gracefully."""
    df = pd.DataFrame(
        columns=["open", "high", "low", "close", "volume"],
        index=pd.DatetimeIndex([], tz="US/Eastern"),
    )
    result = _relabel_futu_hourly_rth_to_bar_open(df)
    assert result.empty


def test_relabel_no_rth_bars():
    """Test that pre-market/after-hours bars are filtered out."""
    data = {
        "open": [100, 101],
        "high": [102, 103],
        "low": [99, 100],
        "close": [101, 102],
        "volume": [1000, 1000],
    }
    # Both timestamps outside RTH window (10:30-16:00)
    index = pd.DatetimeIndex([
        "2021-01-04 08:00:00",  # 08:00 ET (pre-market, before 10:30)
        "2021-01-04 17:00:00",  # 17:00 ET (after-hours, after 16:00)
    ], tz="US/Eastern")

    df = pd.DataFrame(data, index=index)
    result = _relabel_futu_hourly_rth_to_bar_open(df)

    assert result.empty, "Expected all bars to be filtered out"


def test_apply_futu_correction_with_cutoff_date():
    """Test that cutoff_date correctly splits Futu and yfinance data."""
    df = pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2021-01-04T14:30:00Z",  # 09:30 ET - Futu snapshot
            "2021-01-04T15:30:00Z",  # 10:30 ET - Futu (should be corrected)
            "2021-01-04T16:30:00Z",  # 11:30 ET - Futu (should be corrected)
            "2021-01-05T15:30:00Z",  # 10:30 ET next day - yfinance (no correction)
            "2021-01-05T16:30:00Z",  # 11:30 ET next day - yfinance (no correction)
        ], utc=True),
        "open": [99, 100, 101, 200, 201],
        "high": [99, 102, 103, 202, 203],
        "low": [99, 99, 100, 199, 200],
        "close": [99, 101, 102, 201, 202],
        "volume": [0, 1000, 1000, 2000, 2000],
    })

    # Cutoff: 2021-01-04 (only first day should be corrected)
    result = _apply_futu_hourly_correction(df, cutoff_date="2021-01-04")

    # Expected: first 2 bars corrected, last 2 bars unchanged
    expected_timestamps = pd.to_datetime([
        "2021-01-04T14:30:00Z",  # 09:30 ET (corrected from 10:30)
        "2021-01-04T15:30:00Z",  # 10:30 ET (corrected from 11:30)
        "2021-01-05T15:30:00Z",  # 10:30 ET (unchanged - yfinance)
        "2021-01-05T16:30:00Z",  # 11:30 ET (unchanged - yfinance)
    ], utc=True)

    assert len(result) == 4, f"Expected 4 rows, got {len(result)}"
    assert result["timestamp"].tolist() == expected_timestamps.tolist()
    assert result["open"].tolist() == [100, 101, 200, 201]
    assert result["volume"].tolist() == [1000, 1000, 2000, 2000]


def test_unknown_timestamp_semantics_raises_error():
    """Test that unknown timestamp_semantics values are rejected."""
    from pipeline.data_fetcher import load_local_history_by_name
    import pytest

    # Typo in semantics should raise ValueError
    with pytest.raises(ValueError, match="Unknown timestamp_semantics"):
        load_local_history_by_name(
            "MSTR_1h.csv",
            target_bar="1H",
            canonical_only=False,
            timestamp_semantics="futu_close_labelled",  # Typo: extra 'l'
        )


def test_cutoff_without_semantics_raises_error():
    """Test that futu_cutoff_date without timestamp_semantics is rejected."""
    from pipeline.data_fetcher import load_local_history_by_name
    import pytest

    # cutoff_date without semantics should raise ValueError
    with pytest.raises(ValueError, match="futu_cutoff_date specified but timestamp_semantics is missing"):
        load_local_history_by_name(
            "MSTR_1h.csv",
            target_bar="1H",
            canonical_only=False,
            timestamp_semantics=None,
            futu_cutoff_date="2026-04-30",
        )


def test_futu_semantics_on_non_1h_raises_error():
    """Test that futu_close_labeled on non-1H data is rejected."""
    from pipeline.data_fetcher import load_local_history_by_name
    import pytest

    # 4H with Futu semantics should raise ValueError
    with pytest.raises(ValueError, match="only supports 1H data"):
        load_local_history_by_name(
            "MSTR_1h.csv",
            target_bar="4H",
            canonical_only=False,
            timestamp_semantics="futu_close_labeled",
        )
