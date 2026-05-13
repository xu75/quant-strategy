from __future__ import annotations

import json
from pathlib import Path

from core.registry import discover_strategies, load_strategy_module
from core.runner import load_extra_data_sources, load_strategy_data
from pipeline.report import generate_backtest_json


def _load_dualmom_adapter_and_data():
    manifest = next(
        m for m in discover_strategies(Path("strategies"))
        if m.id == "dualmom_b_fastre"
    )
    adapter = load_strategy_module(manifest)
    df = load_strategy_data(manifest, adapter.config, canonical_only=True)
    extra_data = load_extra_data_sources(manifest, canonical_only=True)
    return adapter, df, extra_data


def test_dualmom_backtest_emits_trade_history():
    adapter, df, extra_data = _load_dualmom_adapter_and_data()

    result = adapter.run_backtest(df, adapter.config, extra_data=extra_data)

    assert result.total_trades > 0
    assert result.win_rate > 0
    assert len(result.trades) > 0
    assert all(t.asset in {"QQQ", "SPY"} for t in result.trades)
    assert {t.status for t in result.trades}.issubset({"closed", "open"})
    assert result.trades[-1].status == "open"


def test_dualmom_trade_history_serializes_optional_fields(tmp_path):
    adapter, df, extra_data = _load_dualmom_adapter_and_data()
    result = adapter.run_backtest(df, adapter.config, extra_data=extra_data)
    out = tmp_path / "backtest.json"

    generate_backtest_json(result, out)

    data = json.loads(out.read_text())
    assert data["trades"]
    assert data["trades"][0]["asset"] in {"QQQ", "SPY"}
    assert data["trades"][-1]["status"] == "open"
