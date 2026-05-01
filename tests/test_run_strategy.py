"""Tests for strategy runner data-source selection."""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

import core.runner as runner
from strategies.btc_ma_trend.signal import StrategyConfig


@dataclass
class MockManifest:
    id: str = "btc_ma_trend"


def make_history(start: str = "2025-01-01") -> pd.DataFrame:
    timestamps = pd.date_range(start, periods=3, freq="4h", tz="UTC")
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": [100.0, 101.0, 102.0],
        "high": [101.0, 102.0, 103.0],
        "low": [99.0, 100.0, 101.0],
        "close": [100.0, 101.0, 102.0],
        "volume": [1.0, 1.0, 1.0],
    })


def test_load_strategy_data_uses_extended_history_when_local_missing(monkeypatch):
    """CI must not fall back to only 300 recent candles when local CSV is missing."""
    calls: list[str] = []

    def missing_local():
        calls.append("local")
        raise FileNotFoundError("missing local CSV")

    def extended_history(**kwargs):
        calls.append(f"historical:{kwargs['limit']}")
        return make_history()

    def recent_only(**kwargs):
        calls.append("recent")
        return make_history("2025-02-01")

    monkeypatch.setattr(runner, "load_local_history", missing_local)
    monkeypatch.setattr(runner, "fetch_historical_candles", extended_history)
    monkeypatch.setattr(runner, "fetch_candles", recent_only)

    manifest = MockManifest()
    config = StrategyConfig()
    df = runner.load_strategy_data(manifest, config)

    assert len(df) == 3
    assert calls == ["local", "historical:6000"]


def test_load_strategy_data_merges_local_history_with_recent_data(monkeypatch):
    """Local runs should still extend the checked-in history with recent candles."""
    calls: list[str] = []

    def local_history():
        calls.append("local")
        return make_history()

    def extended_history(**kwargs):
        calls.append("historical")
        return make_history("2024-01-01")

    def recent_only(**kwargs):
        calls.append("recent")
        return make_history("2025-01-01 08:00")

    monkeypatch.setattr(runner, "load_local_history", local_history)
    monkeypatch.setattr(runner, "fetch_historical_candles", extended_history)
    monkeypatch.setattr(runner, "fetch_candles", recent_only)

    manifest = MockManifest()
    config = StrategyConfig()
    df = runner.load_strategy_data(manifest, config)

    assert len(df) == 5
    assert calls == ["local", "recent"]


def test_sync_public_charts_copies_generated_outputs(tmp_path, monkeypatch):
    charts_dir = tmp_path / "data" / "btc_ma_trend" / "charts"
    charts_dir.mkdir(parents=True)
    (charts_dir / "equity.png").write_bytes(b"new-equity")
    (charts_dir / "price_ma.png").write_bytes(b"new-price")

    public_base = tmp_path / "site" / "public" / "charts"
    monkeypatch.setattr(runner, "SITE_PUBLIC_CHARTS", public_base)

    runner.sync_public_charts("btc_ma_trend", charts_dir)

    assert (public_base / "btc_ma_trend" / "equity.png").read_bytes() == b"new-equity"
    assert (public_base / "btc_ma_trend" / "price_ma.png").read_bytes() == b"new-price"
