from __future__ import annotations

import pandas as pd

from pipeline.report import generate_status_json
from strategies.n100_guard_z.signal import Signal, StrategyConfig, compute_signals


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


def test_n100_status_report_includes_execution_layer_fields(tmp_path):
    signal = Signal(
        action="risk_on",
        price=500.0,
        timestamp=pd.Timestamp("2026-05-25", tz="UTC"),
        reason="NDX_INVESTED",
        ma_value=490.0,
        current_etf="513100",
        current_zscore=-1.25,
        current_premium=0.0032,
    )

    status = generate_status_json(
        df=pd.DataFrame({"timestamp": [signal.timestamp], "close": [signal.price]}),
        config=StrategyConfig(),
        in_position=True,
        entry_bar_idx=0,
        output_path=tmp_path / "latest.json",
        current_signal=signal,
    )

    current = status["current_signal"]
    assert current["current_etf"] == "513100"
    assert current["current_zscore"] == -1.25
    assert current["current_premium"] == 0.0032


def test_n100_latest_signal_keeps_rotation_state_when_etfs_start_later():
    dates = pd.date_range("2024-01-01", periods=320, freq="D", tz="UTC")
    qqq_close = pd.Series(range(100, 420), dtype=float).to_numpy()
    spy_close = pd.Series(range(100, 420), dtype=float).mul(0.5).add(100).to_numpy()
    qqq = pd.DataFrame({"timestamp": dates, "close": qqq_close})
    spy = pd.DataFrame({"timestamp": dates, "close": spy_close})

    etf_dates = dates[60:]
    nav = pd.Series(range(100, 360), dtype=float).reset_index(drop=True)
    premium_wave = pd.Series([0.002, 0.004, -0.003, 0.001, -0.002] * 52)
    etf_close = nav * (1 + premium_wave.iloc[: len(nav)].reset_index(drop=True))
    etf = pd.DataFrame({"timestamp": etf_dates, "close": etf_close})
    nav_df = pd.DataFrame({"timestamp": etf_dates, "513100": nav})

    config = StrategyConfig(
        sma_window=20,
        momentum_lookback_months=1,
        zscore_lookback=5,
        ipo_warmup=5,
    )

    signals = compute_signals(
        qqq,
        config,
        extra_data={
            "spy": spy,
            "etf_513100": etf,
            "etf_nav": nav_df,
        },
    )

    latest = signals[-1]
    assert latest.action == "risk_on"
    assert latest.current_etf == "513100"
