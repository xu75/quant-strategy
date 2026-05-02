#!/usr/bin/env python3
"""Three-factor sweep: signal interval × execution interval × freeze days.

Supports two signal modes:
- long_close_signal: signal computed on signal_interval, inherited by exec_interval
- exec_close_vs_signal_ma: each exec bar close compared against latest signal MA
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from sweep_btc_ma import (
    HISTORY_DIR,
    infer_bars_per_day,
    load_btc_interval,
    ma_period_for_interval,
    run_execution_against_signal_ma,
    run_signal_on_execution,
)

BTC_TICKER = "BTC-USD"

WINDOWS = [
    {"name": "2y", "start": "2024-01-01", "end": "2026-04-30"},
    {"name": "3y", "start": "2023-01-01", "end": "2026-04-30"},
    {"name": "6y", "start": "2020-01-01", "end": "2026-04-30"},
]

SIGNAL_INTERVALS = ["1h", "4h", "1d"]
EXEC_INTERVALS = ["1h", "4h", "1d"]
FREEZE_DAYS = [0, 2, 4, 5, 7]
FEE_RATE = 0.001


def _parse_ts(value: str, tz: str = "UTC") -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize(tz)
    return ts.tz_convert(tz)


def _end_exclusive(value: str, tz: str = "UTC") -> pd.Timestamp:
    ts = _parse_ts(value, tz)
    if len(value) == 10 and value.count("-") == 2:
        return ts + pd.Timedelta(days=1)
    return ts


def _slice_window(df: pd.DataFrame, start: str, end: str, tz: str = "UTC") -> pd.DataFrame:
    return df[(df.index >= _parse_ts(start, tz)) & (df.index < _end_exclusive(end, tz))].copy()


def main():
    results = []

    for window in WINDOWS:
        print(f"\n=== Window: {window['name']} ===")

        # Load execution intervals
        exec_data = {}
        for exec_int in EXEC_INTERVALS:
            try:
                full = load_btc_interval(HISTORY_DIR, exec_int, "UTC")
                exec_data[exec_int] = _slice_window(full, window["start"], window["end"], "UTC")
                print(f"Loaded exec {exec_int}: {len(exec_data[exec_int])} bars")
            except Exception as e:
                print(f"Failed to load exec {exec_int}: {e}")
                exec_data[exec_int] = pd.DataFrame()

        # Load signal intervals with MA
        signal_data = {}
        for sig_int in SIGNAL_INTERVALS:
            try:
                full = load_btc_interval(HISTORY_DIR, sig_int, "UTC")
                ma_period = ma_period_for_interval(sig_int)
                full = full.copy()
                full["ma"] = full["close"].rolling(window=ma_period, min_periods=ma_period).mean()
                signal_data[sig_int] = _slice_window(full, window["start"], window["end"], "UTC").dropna(subset=["ma"])
                print(f"Loaded signal {sig_int}: {len(signal_data[sig_int])} bars")
            except Exception as e:
                print(f"Failed to load signal {sig_int}: {e}")
                signal_data[sig_int] = pd.DataFrame()

        for sig_int in SIGNAL_INTERVALS:
            signal_df = signal_data.get(sig_int)
            if signal_df is None or signal_df.empty:
                continue

            signal_ma_period = ma_period_for_interval(sig_int)
            signal_bars_per_day = infer_bars_per_day(signal_df, sig_int)

            for exec_int in EXEC_INTERVALS:
                exec_df = exec_data.get(exec_int)
                if exec_df is None or exec_df.empty:
                    continue

                exec_bars_per_day = infer_bars_per_day(exec_df, exec_int)

                for freeze_days in FREEZE_DAYS:
                    # Two signal modes
                    modes = [
                        ("long_close_signal", int(freeze_days * signal_bars_per_day), "signal"),
                        ("exec_close_vs_signal_ma", int(freeze_days * exec_bars_per_day), "execution"),
                    ]

                    for signal_mode, freeze_bars, freeze_basis in modes:
                        if signal_mode == "long_close_signal":
                            result = run_signal_on_execution(
                                exec_df=exec_df,
                                signal_df=signal_df,
                                signal_interval=sig_int,
                                signal_ma_period=signal_ma_period,
                                freeze_bars=freeze_bars,
                                fee_rate=FEE_RATE,
                            )
                        else:  # exec_close_vs_signal_ma
                            result = run_execution_against_signal_ma(
                                exec_df=exec_df,
                                signal_df=signal_df,
                                signal_interval=sig_int,
                                signal_ma_period=signal_ma_period,
                                freeze_bars=freeze_bars,
                                fee_rate=FEE_RATE,
                            )

                        results.append({
                            "window": window["name"],
                            "signal_interval": sig_int,
                            "exec_interval": exec_int,
                            "signal_mode": signal_mode,
                            "freeze_days": freeze_days,
                            "freeze_bars": freeze_bars,
                            "freeze_basis": freeze_basis,
                            "return": result.total_return,
                            "max_dd": result.max_dd,
                            "trades": result.trades,
                        })

                        print(f"  {sig_int} sig + {exec_int} exec + {freeze_days}D ({signal_mode}): "
                              f"return={result.total_return:.2%}, trades={result.trades}")

    df = pd.DataFrame(results)
    output = Path("outputs/sweep_three_factor.csv")
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output, index=False)
    print(f"\nSaved: {output}")

    # Compute robustness ranking for each signal mode
    for mode in ["long_close_signal", "exec_close_vs_signal_ma"]:
        mode_df = df[df["signal_mode"] == mode]
        pivot = mode_df.pivot_table(
            index=["signal_interval", "exec_interval", "freeze_days"],
            columns="window",
            values="return",
        )
        pivot["mean_return"] = pivot.mean(axis=1)
        pivot["std_return"] = pivot.std(axis=1)
        pivot["min_return"] = pivot.min(axis=1)

        top = pivot.sort_values("mean_return", ascending=False).head(10)
        print(f"\n=== Top 10 by mean return ({mode}) ===")
        print(top[["2y", "3y", "6y", "mean_return", "std_return", "min_return"]])


if __name__ == "__main__":
    main()
