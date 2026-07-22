---
title: "Futu K_60M Event-Time Fix Migration"
date: 2026-07-23
status: completed
strategy: echotrend_240_v3
source_repo: mstr-strategy-clowder
source_commit: 16b8156
feature_ids: []
topics: [data-quality, event-time, backtest-accuracy]
doc_kind: migration
created: 2026-07-23
---

# Futu K_60M Event-Time Fix Migration

**Date**: 2026-07-23
**Source**: mstr-strategy-clowder (commit 16b8156)
**Target**: quant-strategy repo
**Strategy**: EchoTrend 240 V3
**Status**: ✅ Completed and Verified

## Background

The mstr-strategy-clowder repo identified and fixed a critical event-time alignment issue with Futu MSTR K_60M data:

- **Problem**: Futu's 1H bars are timestamped at candle **close** (e.g., 10:30 label = 09:30-10:30 bar), but backtests execute at `row.open`. Using close labels exposes the open price to signals known only an hour later (look-ahead bias).
- **Impact**: MaxDD improved from -71.6% to -65.86% (5.75pp reduction) after fixing the event-time misalignment.
- **Root Cause**: Close-labeled timestamps + decision-row execution model = implicit 1-hour look-ahead.

## Migration Summary

### 1. Core Functions Added

**`pipeline/data_fetcher.py`**:
- `_relabel_futu_hourly_rth_to_bar_open(df)`: Shifts Futu close-labeled RTH bars to executable bar-open time
  - 10:30 label → 09:30 (shift -1h for full-hour bars)
  - 16:00 label → 15:30 (shift -30min for half-hour closing bar)
  - Filters to RTH window (10:30-16:00 labels, which become 09:30-15:30 after shift)
- `_apply_futu_hourly_correction(df, cutoff_date=None)`: Wrapper that handles timestamp column format and mixed data sources
  - Converts UTC ↔ US/Eastern timezone
  - Supports `cutoff_date` to split Futu (corrected) and yfinance (unchanged) sections

### 2. Configuration Contract

**`manifest.yaml`** data sources now support `timestamp_semantics` and `futu_cutoff_date` fields:

```yaml
data_sources:
  mstr:
    symbol: "MSTR"
    timeframe: "1H"
    source: "local"
    local_file: "MSTR_1h.csv"
    timestamp_semantics: "futu_close_labeled"
    futu_cutoff_date: "2026-04-30"  # Futu data ends, yfinance begins 2026-05-01
```

**Supported values**:
- `timestamp_semantics`: `"futu_close_labeled"` (apply correction) or `null` (no correction)
- `futu_cutoff_date`: `"YYYY-MM-DD"` (only correct data on or before this date)

**Key Design Decision**: Mixed data source handling
- Canonical `MSTR_1h.csv` contains Futu data (close-labeled) through 2026-04-30
- From 2026-05-01 onward, data is from yfinance (bar-open labeled)
- Correction only applies to Futu section; yfinance section remains unchanged
- This prevents double-correction of daily-updated yfinance bars

### 3. Integration Points

**`core/runner.py`**:
- `load_strategy_data()`: Passes both `timestamp_semantics` and `futu_cutoff_date` from manifest to `load_local_history_by_name()`
- `load_extra_data_sources()`: Same for extra data sources

**`pipeline/data_fetcher.py`**:
- `load_local_history_by_name()`: Accepts both parameters and applies segmented correction when `timestamp_semantics == "futu_close_labeled" and target_bar == "1h"`

### 4. State Migration

**Strategy version bump**: `1.0.0` → `1.1.0`
- Event-time contract change requires existing state to be invalidated
- `core/state.py` validates `strategy_version` in `validate_state_for_resume()`
- Version mismatch causes daily-signal mode to skip (fail-closed), preventing use of old state
- Scheduled workflow defaults to full-backtest, which will rebuild state with corrected event-time data
- This ensures historical event-time fix is absorbed into new state

### 5. Test Coverage

**`tests/test_futu_timestamp_correction.py`** (8 tests, all passing ✅):
- `test_relabel_futu_hourly_rth_to_bar_open()`: Core logic with US/Eastern indexed data
- `test_apply_futu_hourly_correction_with_timestamp_column()`: Wrapper with UTC timestamp column
- `test_apply_futu_correction_with_cutoff_date()`: Segmented correction for mixed sources
- `test_relabel_empty_dataframe()`: Edge case handling
- `test_relabel_no_rth_bars()`: Pre-market/after-hours filtering
- `test_unknown_timestamp_semantics_raises_error()`: Typo protection (fail-closed)
- `test_cutoff_without_semantics_raises_error()`: Invalid combination rejected
- `test_futu_semantics_on_non_1h_raises_error()`: Non-1H data rejected

**`tests/test_run_strategy.py`**:
- Updated monkeypatch to support new parameters (fixes CI failure)
- `test_manifest_timestamp_semantics_passed_to_loader()`: Manifest→runner wiring verification

**All tests pass**: 243 passed ✅

### 6. Verification Results

Ran full backtest on EchoTrend 240 V3 with corrected data:

```
Data split verification:
  Futu section (corrected):     11,130 bars ending 2026-04-30 19:30 UTC
  yfinance section (unchanged):    385 bars starting 2026-05-01 13:30 UTC
  Total:                        11,515 bars

Backtest Results:
  Total Return:    3277.95%
  Max Drawdown:      65.81%  ← Target achieved!
  Sharpe Ratio:       1.23
  Win Rate:          43.48%
  Total Trades:         92
  Period:         2020-01-02 to 2026-07-21
```

**Target MaxDD achieved**: 65.81% vs. expected 65.86% (0.05pp difference likely due to data updates)

## Impact Analysis

| Metric | Before Fix | After Fix | Change |
|--------|------------|-----------|--------|
| MaxDD | -71.6% | -65.81% | **+5.79pp** ✅ |

The fix eliminates implicit 1-hour look-ahead bias in decision-row execution model, making backtest results more realistic and conservative.

## Migration Checklist

- [x] Core function `_relabel_futu_hourly_rth_to_bar_open()` added
- [x] Wrapper `_apply_futu_hourly_correction()` added with cutoff_date support
- [x] `timestamp_semantics` and `futu_cutoff_date` fields added to manifest
- [x] `load_strategy_data()` updated to pass both parameters
- [x] `load_extra_data_sources()` updated to pass both parameters
- [x] `load_local_history_by_name()` updated to apply segmented correction
- [x] Test suite added with cutoff_date coverage
- [x] All tests passing (243/243)
- [x] Full backtest verified MaxDD improvement
- [x] CI compatibility fixed (monkeypatch updated)
- [x] Strategy version bumped to invalidate old state

## Key Fixes from Review

1. **Removed QQQ correction**: Only MSTR uses Futu data; QQQ correction was out of scope
2. **Added cutoff_date support**: Handles mixed Futu/yfinance data in canonical CSV
3. **Fixed CI test failure**: Updated monkeypatch to accept new parameters
4. **Verified segmented correction**: Only Futu section (≤2026-04-30) is corrected
5. **State migration**: Version bump ensures old state is invalidated and scheduled workflow defaults to full-backtest

## References

- Source commit: `mstr-strategy-clowder` @ 16b8156
- Forensics doc: `docs/v3-futu-drawdown-root-cause.md` (in source repo)
- PR: mstr-strategy-clowder PR #1