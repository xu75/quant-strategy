from __future__ import annotations

import pandas as pd

from pipeline.report import generate_status_json
from strategies.n100_guard_z.signal import Signal, StrategyConfig


def test_n100_signal_satisfies_status_report_contract(tmp_path):
    signal = Signal(
        action="risk_off",
        price=500.0,
        timestamp=pd.Timestamp("2026-05-25", tz="UTC"),
        reason="TRUE_CASH_STRETCH",
        ma_value=490.0,
    )

    status = generate_status_json(
        df=pd.DataFrame({"timestamp": [signal.timestamp], "close": [signal.price]}),
        config=StrategyConfig(),
        in_position=False,
        entry_bar_idx=0,
        output_path=tmp_path / "latest.json",
        current_signal=signal,
    )

    assert status["current_signal"]["hold_bars"] == 0
