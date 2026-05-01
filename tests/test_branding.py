"""Branding regression tests for public strategy/site copy."""

from pathlib import Path

import pandas as pd

from pipeline.report import generate_status_json
from strategies.btc_ma_trend.signal import StrategyConfig


ROOT = Path(__file__).resolve().parents[1]


def test_status_json_uses_public_strategy_name(tmp_path):
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=2, freq="4h", tz="UTC"),
        "close": [100.0, 101.0],
    })

    status = generate_status_json(
        df=df,
        config=StrategyConfig(),
        in_position=False,
        entry_bar_idx=0,
        output_path=tmp_path / "latest.json",
    )

    assert status["strategy"]["name"] == "TrendLock 40"
    assert status["strategy"]["code"] == "T40-4"


def test_public_site_copy_uses_quant_strategy_brand():
    public_copy_files = [
        "site/src/layouts/Layout.astro",
        "site/src/pages/index.astro",
        "site/src/pages/disclaimer.astro",
        "site/src/i18n/translations.ts",
        "pyproject.toml",
    ]

    combined = ""
    for rel_path in public_copy_files:
        text = (ROOT / rel_path).read_text()
        combined += text
        assert "MeshHub" not in text, rel_path

    assert "Quant Strategy" in combined
