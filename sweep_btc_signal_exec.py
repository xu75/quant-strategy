#!/usr/bin/env python3
"""Sweep 1H execution with 1H/4H/1D BTC MA signal cadences."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from sweep_btc_ma import (
    DEFAULT_FREEZE_DAYS,
    HISTORY_DIR,
    infer_bars_per_day,
    load_btc_interval,
    ma_period_for_interval,
    run_execution_against_signal_ma,
    run_signal_on_execution,
)


DEFAULT_WINDOWS = [
    {"name": "1h_exec_2y", "start": "2024-04-21", "end": "2026-04-28"},
    {"name": "1h_exec_3y", "start": "2023-01-01", "end": "2026-04-28"},
    {"name": "1h_exec_6y", "start": "2020-01-01", "end": "2026-04-28"},
]
DEFAULT_SIGNAL_INTERVALS = ["1h", "4h", "1d"]


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


def _parse_int_list(value: str) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def _parse_str_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _select_windows(names: str | None) -> list[dict]:
    if not names:
        return DEFAULT_WINDOWS
    wanted = set(_parse_str_list(names))
    selected = [window for window in DEFAULT_WINDOWS if window["name"] in wanted]
    missing = wanted - {window["name"] for window in selected}
    if missing:
        raise ValueError(f"Unknown windows: {', '.join(sorted(missing))}")
    return selected


def run_sweep(
    history_dir: Path,
    windows: list[dict],
    signal_intervals: list[str],
    freeze_days: list[int],
    fee_rate: float,
    tz: str = "UTC",
) -> pd.DataFrame:
    exec_full = load_btc_interval(history_dir, "1h", tz)
    signal_full_by_interval = {}
    for interval in signal_intervals:
        signal_full = load_btc_interval(history_dir, interval, tz).copy()
        ma_period = ma_period_for_interval(interval)
        signal_full["ma"] = signal_full["close"].rolling(
            window=ma_period,
            min_periods=ma_period,
        ).mean()
        signal_full_by_interval[interval] = signal_full

    rows = []
    for window in windows:
        exec_df = _slice_window(exec_full, window["start"], window["end"], tz)
        if exec_df.empty:
            raise ValueError(f"No 1H execution data in window {window['name']}")
        exec_bars_per_day = infer_bars_per_day(exec_df, "1h")

        for signal_interval in signal_intervals:
            signal_full = signal_full_by_interval[signal_interval]
            signal_df = _slice_window(signal_full, window["start"], window["end"], tz)
            signal_ma_period = ma_period_for_interval(signal_interval)
            signal_bars_per_day = infer_bars_per_day(signal_df, signal_interval)

            for freeze_day in freeze_days:
                freeze_signal_bars = int(freeze_day * signal_bars_per_day)
                freeze_exec_bars = int(freeze_day * exec_bars_per_day)
                mode_runs = [
                    (
                        "long_close_signal",
                        freeze_signal_bars,
                        "signal",
                        run_signal_on_execution(
                            exec_df=exec_df,
                            signal_df=signal_df,
                            signal_interval=signal_interval,
                            signal_ma_period=signal_ma_period,
                            freeze_bars=freeze_signal_bars,
                            fee_rate=fee_rate,
                        ),
                    ),
                    (
                        "exec_close_vs_signal_ma",
                        freeze_exec_bars,
                        "execution",
                        run_execution_against_signal_ma(
                            exec_df=exec_df,
                            signal_df=signal_df,
                            signal_interval=signal_interval,
                            signal_ma_period=signal_ma_period,
                            freeze_bars=freeze_exec_bars,
                            fee_rate=fee_rate,
                        ),
                    ),
                ]
                for signal_mode, freeze_bars, freeze_basis, result in mode_runs:
                    rows.append(
                        {
                            "window": window["name"],
                            "exec_interval": "1h",
                            "signal_interval": signal_interval,
                            "signal_mode": signal_mode,
                            "signal_ma_period": signal_ma_period,
                            "freeze_days": freeze_day,
                            "freeze_bars": freeze_bars,
                            "freeze_basis": freeze_basis,
                            "raw_flips": result.raw_flips,
                            "regime_flips": result.regime_flips,
                            "trades": result.trades,
                            "return": result.total_return,
                            "max_dd": result.max_dd,
                            "excess_vs_bh": result.excess_vs_bh,
                        }
                    )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sweep 1H execution against 1H/4H/1D BTC MA signals.")
    parser.add_argument("--history-dir", type=Path, default=HISTORY_DIR)
    parser.add_argument("--windows", default=None, help="Comma-separated window names.")
    parser.add_argument("--signal-intervals", default=",".join(DEFAULT_SIGNAL_INTERVALS))
    parser.add_argument("--freeze-days", default=",".join(str(x) for x in DEFAULT_FREEZE_DAYS))
    parser.add_argument("--fee-rate", type=float, default=0.001)
    parser.add_argument("--output", type=Path, default=Path("outputs/sweep_btc_signal_exec.csv"))
    args = parser.parse_args()

    result = run_sweep(
        history_dir=args.history_dir,
        windows=_select_windows(args.windows),
        signal_intervals=_parse_str_list(args.signal_intervals),
        freeze_days=_parse_int_list(args.freeze_days),
        fee_rate=args.fee_rate,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output, index=False)
    print(result.to_string(index=False))
    print(f"\nSaved: {args.output}")


if __name__ == "__main__":
    main()
