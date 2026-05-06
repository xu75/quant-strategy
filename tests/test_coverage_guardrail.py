"""Coverage guardrail tests — fail-closed validation for data pipeline.

These tests verify that:
1. Full-backtest refuses to run when data doesn't cover min_lookback_years + warmup
2. backtest.json includes provenance metadata
3. Period metrics are not output when data coverage is insufficient
4. Manifests declare required coverage fields
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
import yaml

PROJECT_ROOT = Path(__file__).parent.parent
STRATEGIES_DIR = PROJECT_ROOT / "strategies"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_df(start: str, periods: int, freq_hours: int = 4) -> pd.DataFrame:
    """Create a minimal OHLCV DataFrame."""
    timestamps = pd.date_range(start, periods=periods, freq=f"{freq_hours}h", tz="UTC")
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": 100.0,
        "high": 105.0,
        "low": 95.0,
        "close": 100.0,
        "volume": 1000.0,
    })


def _load_all_manifests() -> list[tuple[Path, dict]]:
    results = []
    for p in sorted(STRATEGIES_DIR.glob("*/manifest.yaml")):
        with open(p) as f:
            results.append((p, yaml.safe_load(f)))
    return results


# ---------------------------------------------------------------------------
# Test 1: Manifest must declare min_lookback_years and warmup_bars
# ---------------------------------------------------------------------------

class TestManifestCoverageFields:
    """Every enabled strategy must declare coverage requirements."""

    @pytest.fixture(scope="class")
    def manifests(self):
        return _load_all_manifests()

    def test_min_lookback_years_present(self, manifests):
        for path, data in manifests:
            if not data.get("enabled", True):
                continue
            assert "min_lookback_years" in data, (
                f"{path}: missing required field 'min_lookback_years'"
            )
            assert isinstance(data["min_lookback_years"], int), (
                f"{path}: min_lookback_years must be an integer"
            )
            assert data["min_lookback_years"] >= 1, (
                f"{path}: min_lookback_years must be >= 1"
            )

    def test_warmup_bars_present(self, manifests):
        for path, data in manifests:
            if not data.get("enabled", True):
                continue
            assert "warmup_bars" in data, (
                f"{path}: missing required field 'warmup_bars'"
            )
            assert isinstance(data["warmup_bars"], int), (
                f"{path}: warmup_bars must be an integer"
            )
            assert data["warmup_bars"] >= 1, (
                f"{path}: warmup_bars must be >= 1"
            )

    def test_data_sources_declared(self, manifests):
        """Every enabled strategy must have data_sources with a local_file for primary."""
        for path, data in manifests:
            if not data.get("enabled", True):
                continue
            assert "data_sources" in data, (
                f"{path}: missing 'data_sources' — full-backtest requires canonical data"
            )
            primary_symbol = data["config"]["symbol"]
            found_primary = False
            for src in data["data_sources"].values():
                if src.get("symbol") == primary_symbol:
                    assert "local_file" in src, (
                        f"{path}: primary data source for {primary_symbol} must declare local_file"
                    )
                    found_primary = True
                    break
            assert found_primary, (
                f"{path}: no data_source entry matches primary symbol '{primary_symbol}'"
            )


# ---------------------------------------------------------------------------
# Test 2: Coverage validation logic
# ---------------------------------------------------------------------------

class TestCoverageValidation:
    """Runner must reject data that doesn't meet coverage requirements."""

    def test_validate_coverage_passes_with_sufficient_data(self):
        from core.runner import validate_data_coverage
        df = _make_df("2019-01-01", periods=13000, freq_hours=4)
        # 5 years + 240 bars warmup at 4H should pass
        validate_data_coverage(df, min_lookback_years=5, warmup_bars=240, timeframe="4H")

    def test_validate_coverage_fails_with_insufficient_data(self):
        from core.runner import validate_data_coverage
        # Only 1 year of data, but requires 5 years
        df = _make_df("2025-01-01", periods=2190, freq_hours=4)
        with pytest.raises(ValueError, match="coverage"):
            validate_data_coverage(df, min_lookback_years=5, warmup_bars=240, timeframe="4H")

    def test_validate_coverage_accounts_for_warmup(self):
        from core.runner import validate_data_coverage
        # Exactly 5 years but no warmup margin
        start = pd.Timestamp.now(tz="UTC") - pd.DateOffset(years=5)
        periods = int(5 * 365.25 * 24 / 4)
        df = _make_df(start.strftime("%Y-%m-%d"), periods=periods, freq_hours=4)
        with pytest.raises(ValueError, match="coverage"):
            validate_data_coverage(df, min_lookback_years=5, warmup_bars=240, timeframe="4H")

    def test_extra_source_uses_wall_clock_not_own_timeframe(self):
        """Extra daily source should NOT multiply warmup_bars by its own timeframe.

        EchoTrend has warmup_bars=960 at 1H primary. A daily extra source starting
        2020-01-01 should pass because the wall-clock required_start is based on
        960 * 1H = 40 days warmup, not 960 * 24H = 960 days.
        """
        from core.runner import _hours_per_bar
        # Simulate: primary 1H data ending 2026-05-06, min_lookback=5, warmup=960 bars @ 1H
        # required_start = 2026-05-06 - 5y - 960*1h = 2021-05-06 - 40 days = ~2021-03-27
        # A daily source starting 2020-01-01 should easily pass
        primary_end = pd.Timestamp("2026-05-06", tz="UTC")
        min_lookback = 5
        warmup = 960
        primary_timeframe = "1H"
        warmup_hours = warmup * _hours_per_bar(primary_timeframe)
        required_start = primary_end - pd.DateOffset(years=min_lookback) - pd.Timedelta(hours=warmup_hours)

        # Daily extra source starting 2020-01-02 (like MSTR daily)
        extra_start = pd.Timestamp("2020-01-02", tz="UTC")
        assert extra_start <= required_start, (
            f"Daily source starting {extra_start.date()} should pass "
            f"wall-clock boundary {required_start.date()}"
        )

        # But if we wrongly used warmup * daily_hours: 960 * 24 = 23040 hours
        wrong_warmup_hours = warmup * _hours_per_bar("1D")
        wrong_required = primary_end - pd.DateOffset(years=min_lookback) - pd.Timedelta(hours=wrong_warmup_hours)
        assert extra_start > wrong_required, (
            "This confirms the old logic would have wrongly rejected the daily source"
        )


# ---------------------------------------------------------------------------
# Test 3: Provenance in backtest.json
# ---------------------------------------------------------------------------

class TestBacktestProvenance:
    """backtest.json must include provenance metadata."""

    def test_generate_backtest_json_includes_provenance(self, tmp_path):
        from dataclasses import dataclass
        from pipeline.backtest import BacktestResult
        from pipeline.report import generate_backtest_json

        @dataclass
        class _MockConfig:
            display_name: str = "Test"
            internal_code: str = "test"
            timeframe: str = "4H"
            symbol: str = "BTC-USDT"

        result = BacktestResult(
            config=_MockConfig(),
            trades=[],
            equity_curve=pd.DataFrame({"timestamp": [], "equity": []}),
            total_return_pct=0.0,
            realized_return_pct=0.0,
            max_drawdown_pct=0.0,
            win_rate=0.0,
            total_trades=0,
            avg_hold_bars=0.0,
            sharpe_ratio=0.0,
            start_date=pd.Timestamp("2020-01-01", tz="UTC"),
            end_date=pd.Timestamp("2026-05-06", tz="UTC"),
            buy_hold_return_pct=0.0,
            buy_hold_max_drawdown_pct=0.0,
            has_open_position=False,
        )

        provenance = {
            "generation_mode": "full-backtest",
            "coverage_ok": True,
            "min_lookback_years": 5,
            "sources": [
                {
                    "key": "primary",
                    "symbol": "BTC-USDT",
                    "timeframe": "4H",
                    "source": "canonical",
                    "rows": 10000,
                    "start": "2020-01-01T00:00:00+00:00",
                    "end": "2026-05-06T12:00:00+00:00",
                },
            ],
        }

        out = tmp_path / "backtest.json"
        generate_backtest_json(result, out, provenance=provenance)

        data = json.loads(out.read_text())
        assert "provenance" in data
        assert data["provenance"]["coverage_ok"] is True
        assert data["provenance"]["generation_mode"] == "full-backtest"
        assert data["provenance"]["min_lookback_years"] == 5
        assert len(data["provenance"]["sources"]) == 1
        assert data["provenance"]["sources"][0]["source"] == "canonical"
        assert data["provenance"]["sources"][0]["rows"] == 10000


# ---------------------------------------------------------------------------
# Test 4: Period metrics coverage guard
# ---------------------------------------------------------------------------

class TestPeriodCoverageGuard:
    """Period metrics must not be output when data doesn't cover the window."""

    def test_period_skipped_when_data_too_short(self):
        """If data starts in 2024, a '5y' period should not appear."""
        from core.runner import build_performance_period_boundaries, filter_periods_by_coverage

        end_date = pd.Timestamp("2026-05-06", tz="UTC")
        data_start = pd.Timestamp("2024-01-01", tz="UTC")
        boundaries = build_performance_period_boundaries(end_date)

        valid_periods = filter_periods_by_coverage(boundaries, data_start)

        assert "5y" not in valid_periods
        assert "3y" not in valid_periods
        assert "1y" in valid_periods

    def test_all_periods_present_when_data_sufficient(self):
        """If data starts in 2019, all periods should be present."""
        from core.runner import build_performance_period_boundaries, filter_periods_by_coverage

        end_date = pd.Timestamp("2026-05-06", tz="UTC")
        data_start = pd.Timestamp("2019-01-01", tz="UTC")
        boundaries = build_performance_period_boundaries(end_date)

        valid_periods = filter_periods_by_coverage(boundaries, data_start)

        assert "5y" in valid_periods
        assert "3y" in valid_periods
        assert "2y" in valid_periods
        assert "1y" in valid_periods
