#!/usr/bin/env python3
"""Sweep BTC MA trend-gate execution interval and freeze settings.

The strategy is intentionally minimal:
- signal: close > MA => target exposure 1.0, close < MA => target exposure 0.0
- execution: signal observed at bar N close, executed at bar N+1 open
- freeze: after a confirmed regime flip, ignore opposite raw signals for N bars
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


HISTORY_DIR = Path("/Users/xujinsong/VSCode/SynologyDrive/backtest/history_data")
MSTR_STRATEGY_DIR = Path("/Users/xujinsong/VSCode/SynologyDrive/backtest/mstr-strategy-clowder")
BTC_TICKER = "BTC-USD"
DEFAULT_FREEZE_DAYS = [0, 1, 2, 3, 5, 7, 10, 15, 20]
DEFAULT_WINDOWS = [
    {"name": "4h_3y", "interval": "4h", "start": "2023-01-01", "end": "2026-04-28"},
    {"name": "4h_6y", "interval": "4h", "start": "2020-01-01", "end": "2026-04-28"},
    {"name": "1h_2y", "interval": "1h", "start": "2024-04-21", "end": "2026-04-28"},
    {"name": "1d_3y", "interval": "1d", "start": "2023-01-01", "end": "2026-04-28"},
    {"name": "1d_6y", "interval": "1d", "start": "2020-01-01", "end": "2026-04-28"},
]


@dataclass
class SimulationResult:
    raw_flips: int
    regime_flips: int
    trades: int
    total_return: float
    max_dd: float
    buy_hold_return: float
    excess_vs_bh: float
    executions: list[dict]
    equity_curve: pd.DataFrame


def _import_history_loader():
    if str(MSTR_STRATEGY_DIR) not in sys.path:
        sys.path.insert(0, str(MSTR_STRATEGY_DIR))
    from src.data_loader import load_history_interval

    return load_history_interval


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


def _interval_hours(interval: str) -> float:
    value = interval.strip().lower()
    if value.endswith("h"):
        return float(value[:-1])
    if value.endswith("d"):
        return float(value[:-1]) * 24.0
    raise ValueError(f"Unsupported interval: {interval}")


def interval_timedelta(interval: str) -> pd.Timedelta:
    return pd.Timedelta(hours=_interval_hours(interval))


def ma_period_for_interval(interval: str, base_period: int = 240, base_interval: str = "4h") -> int:
    """Convert 4H MA240 into an equivalent period for another interval."""
    base_hours = _interval_hours(base_interval)
    target_hours = _interval_hours(interval)
    return max(1, int(round(base_period * base_hours / target_hours)))


def infer_bars_per_day(df: pd.DataFrame, interval: str) -> int:
    """Infer bars/day from the actual data density, not a market-hours guess."""
    if interval.strip().lower().endswith("d"):
        return 1
    if df.empty:
        return 1

    index = pd.DatetimeIndex(df.index)
    if index.tz is None:
        index = index.tz_localize("UTC")
    dates = pd.Series(index.tz_convert("UTC").date)
    counts = dates.value_counts()
    if counts.empty:
        return 1
    return max(1, int(counts.mode().max()))


def resample_ohlcv(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Build missing BTC intervals from denser OHLCV history."""
    rule = interval.strip().lower()
    out = (
        df.sort_index()
        .resample(rule, label="left", closed="left", origin="start_day")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["open", "high", "low", "close"])
    )
    return out


def load_btc_interval(history_dir: Path, interval: str, tz: str = "UTC") -> pd.DataFrame:
    """Load BTC history through the shared mstr-strategy data_loader."""
    load_history_interval = _import_history_loader()
    try:
        return load_history_interval(history_dir, BTC_TICKER, interval, tz)
    except FileNotFoundError:
        if interval.strip().lower() != "4h":
            raise
        hourly = load_history_interval(history_dir, BTC_TICKER, "1h", tz)
        return resample_ohlcv(hourly, "4h")


def count_raw_signal_flips(raw_signal: pd.Series) -> int:
    binary = raw_signal.astype(int)
    return int((binary.diff().abs() > 0).sum())


def build_regime(raw_signal: pd.Series, freeze_bars: int) -> tuple[pd.Series, int]:
    """Apply one-bar confirmation plus MSTR-style freeze locking."""
    if raw_signal.empty:
        return raw_signal.astype(int), 0

    current: int | None = None
    last_change_pos = -10**12
    regime_values: list[int] = []
    flips = 0

    for pos, desired_value in enumerate(raw_signal.astype(int).tolist()):
        if current is None:
            current = desired_value
        elif desired_value != current and pos - last_change_pos >= freeze_bars:
            current = desired_value
            last_change_pos = pos
            flips += 1
        regime_values.append(current)

    return pd.Series(regime_values, index=raw_signal.index, dtype=int), flips


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min())


def run_trend_gate(
    df: pd.DataFrame,
    ma_period: int,
    freeze_bars: int,
    initial_capital: float = 1.0,
    fee_rate: float = 0.001,
) -> SimulationResult:
    """Run one BTC MA trend-gate backtest with next-bar execution."""
    work = df.sort_index().copy()
    if "ma" not in work.columns:
        work["ma"] = work["close"].rolling(window=ma_period, min_periods=ma_period).mean()
    work = work.dropna(subset=["ma"])

    if len(work) < 2:
        empty_curve = pd.DataFrame(columns=["timestamp", "equity"])
        return SimulationResult(0, 0, 0, 0.0, 0.0, 0.0, 0.0, [], empty_curve)

    raw_signal = (work["close"] > work["ma"]).astype(int)
    raw_flips = count_raw_signal_flips(raw_signal)
    regime, regime_flips = build_regime(raw_signal, freeze_bars)

    cash = float(initial_capital)
    units = 0.0
    pending_target: int | None = None
    executions: list[dict] = []
    equity_points: list[dict] = []

    for pos, (timestamp, row) in enumerate(work.iterrows()):
        open_price = float(row["open"])
        close_price = float(row["close"])

        if pending_target is not None:
            current_exposure = 1 if units > 0 else 0
            if pending_target != current_exposure:
                if pending_target == 1 and cash > 0:
                    units = cash / (open_price * (1 + fee_rate))
                    cash = 0.0
                    executions.append({"timestamp": timestamp, "side": "buy", "price": open_price})
                elif pending_target == 0 and units > 0:
                    cash = units * open_price * (1 - fee_rate)
                    units = 0.0
                    executions.append({"timestamp": timestamp, "side": "sell", "price": open_price})
            pending_target = None

        equity = cash if units == 0 else units * close_price * (1 - fee_rate)
        equity_points.append({"timestamp": timestamp, "equity": equity})

        if pos < len(work) - 1:
            desired_exposure = int(regime.iloc[pos])
            current_exposure = 1 if units > 0 else 0
            if desired_exposure != current_exposure:
                pending_target = desired_exposure

    equity_curve = pd.DataFrame(equity_points)
    total_return = float(equity_curve.iloc[-1]["equity"] / initial_capital - 1.0)
    max_dd = _max_drawdown(equity_curve["equity"])

    first_open = float(work.iloc[0]["open"])
    last_close = float(work.iloc[-1]["close"])
    buy_hold_return = last_close / first_open - 1.0

    return SimulationResult(
        raw_flips=raw_flips,
        regime_flips=regime_flips,
        trades=len(executions),
        total_return=total_return,
        max_dd=max_dd,
        buy_hold_return=buy_hold_return,
        excess_vs_bh=total_return - buy_hold_return,
        executions=executions,
        equity_curve=equity_curve,
    )


def run_signal_on_execution(
    exec_df: pd.DataFrame,
    signal_df: pd.DataFrame,
    signal_interval: str,
    signal_ma_period: int,
    freeze_bars: int,
    initial_capital: float = 1.0,
    fee_rate: float = 0.001,
) -> SimulationResult:
    """Run long-cadence MA signals on a shorter execution dataframe.

    Signal bars are labeled by their open time. A signal observed at a signal
    bar close becomes executable at the first execution bar whose open is at
    or after signal_open + signal_interval.
    """
    exec_work = exec_df.sort_index().copy()
    sig_work = signal_df.sort_index().copy()
    if "ma" not in sig_work.columns:
        sig_work["ma"] = sig_work["close"].rolling(
            window=signal_ma_period,
            min_periods=signal_ma_period,
        ).mean()
    sig_work = sig_work.dropna(subset=["ma"])

    if exec_work.empty or sig_work.empty:
        empty_curve = pd.DataFrame(columns=["timestamp", "equity"])
        return SimulationResult(0, 0, 0, 0.0, 0.0, 0.0, 0.0, [], empty_curve)

    raw_signal = (sig_work["close"] > sig_work["ma"]).astype(int)
    raw_flips = count_raw_signal_flips(raw_signal)
    regime, regime_flips = build_regime(raw_signal, freeze_bars)

    effective_index = pd.DatetimeIndex(regime.index) + interval_timedelta(signal_interval)
    regime_events = pd.Series(regime.to_numpy(), index=effective_index).sort_index()
    exec_index = pd.DatetimeIndex(exec_work.index)
    target_by_exec = (
        regime_events.reindex(regime_events.index.union(exec_index))
        .sort_index()
        .ffill()
        .reindex(exec_index)
    )

    cash = float(initial_capital)
    units = 0.0
    executions: list[dict] = []
    equity_points: list[dict] = []

    for timestamp, row in exec_work.iterrows():
        open_price = float(row["open"])
        close_price = float(row["close"])
        target = target_by_exec.loc[timestamp]

        if pd.notna(target):
            desired_exposure = int(target)
            current_exposure = 1 if units > 0 else 0
            if desired_exposure != current_exposure:
                if desired_exposure == 1 and cash > 0:
                    units = cash / (open_price * (1 + fee_rate))
                    cash = 0.0
                    executions.append({"timestamp": timestamp, "side": "buy", "price": open_price})
                elif desired_exposure == 0 and units > 0:
                    cash = units * open_price * (1 - fee_rate)
                    units = 0.0
                    executions.append({"timestamp": timestamp, "side": "sell", "price": open_price})

        equity = cash if units == 0 else units * close_price * (1 - fee_rate)
        equity_points.append({"timestamp": timestamp, "equity": equity})

    equity_curve = pd.DataFrame(equity_points)
    total_return = float(equity_curve.iloc[-1]["equity"] / initial_capital - 1.0)
    max_dd = _max_drawdown(equity_curve["equity"])
    first_open = float(exec_work.iloc[0]["open"])
    last_close = float(exec_work.iloc[-1]["close"])
    buy_hold_return = last_close / first_open - 1.0

    return SimulationResult(
        raw_flips=raw_flips,
        regime_flips=regime_flips,
        trades=len(executions),
        total_return=total_return,
        max_dd=max_dd,
        buy_hold_return=buy_hold_return,
        excess_vs_bh=total_return - buy_hold_return,
        executions=executions,
        equity_curve=equity_curve,
    )


def run_execution_against_signal_ma(
    exec_df: pd.DataFrame,
    signal_df: pd.DataFrame,
    signal_interval: str,
    signal_ma_period: int,
    freeze_bars: int,
    initial_capital: float = 1.0,
    fee_rate: float = 0.001,
) -> SimulationResult:
    """Evaluate each execution close against the latest known long-cadence MA."""
    exec_work = exec_df.sort_index().copy()
    sig_work = signal_df.sort_index().copy()
    if "ma" not in sig_work.columns:
        sig_work["ma"] = sig_work["close"].rolling(
            window=signal_ma_period,
            min_periods=signal_ma_period,
        ).mean()
    sig_work = sig_work.dropna(subset=["ma"])

    if exec_work.empty or sig_work.empty:
        empty_curve = pd.DataFrame(columns=["timestamp", "equity"])
        return SimulationResult(0, 0, 0, 0.0, 0.0, 0.0, 0.0, [], empty_curve)

    effective_index = pd.DatetimeIndex(sig_work.index) + interval_timedelta(signal_interval)
    ma_events = pd.Series(sig_work["ma"].to_numpy(), index=effective_index).sort_index()
    exec_index = pd.DatetimeIndex(exec_work.index)
    exec_work["ma"] = (
        ma_events.reindex(ma_events.index.union(exec_index))
        .sort_index()
        .ffill()
        .reindex(exec_index)
    )

    return run_trend_gate(
        exec_work,
        ma_period=signal_ma_period,
        freeze_bars=freeze_bars,
        initial_capital=initial_capital,
        fee_rate=fee_rate,
    )


def _slice_window(df: pd.DataFrame, start: str, end: str, tz: str = "UTC") -> pd.DataFrame:
    start_ts = _parse_ts(start, tz)
    end_ts = _end_exclusive(end, tz)
    return df[(df.index >= start_ts) & (df.index < end_ts)].copy()


def run_sweep_for_window(
    history_dir: Path,
    window: dict,
    freeze_days: list[int],
    fee_rate: float,
    tz: str = "UTC",
) -> pd.DataFrame:
    interval = window["interval"]
    full = load_btc_interval(history_dir, interval, tz)
    ma_period = ma_period_for_interval(interval)
    full = full.copy()
    full["ma"] = full["close"].rolling(window=ma_period, min_periods=ma_period).mean()
    window_df = _slice_window(full, window["start"], window["end"], tz)
    if window_df.empty:
        raise ValueError(f"No data in window {window['name']}")

    bars_per_day = infer_bars_per_day(window_df, interval)

    rows = []
    for freeze_day in freeze_days:
        freeze_bars = int(freeze_day * bars_per_day)
        result = run_trend_gate(window_df, ma_period=ma_period, freeze_bars=freeze_bars, fee_rate=fee_rate)
        rows.append(
            {
                "window": window["name"],
                "interval": interval,
                "ma_period": ma_period,
                "freeze_days": freeze_day,
                "freeze_bars": freeze_bars,
                "raw_flips": result.raw_flips,
                "regime_flips": result.regime_flips,
                "trades": result.trades,
                "return": result.total_return,
                "max_dd": result.max_dd,
                "excess_vs_bh": result.excess_vs_bh,
            }
        )
    return pd.DataFrame(rows)


def _parse_int_list(value: str) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def _select_windows(names: str | None) -> list[dict]:
    if not names:
        return DEFAULT_WINDOWS
    wanted = {name.strip() for name in names.split(",") if name.strip()}
    selected = [window for window in DEFAULT_WINDOWS if window["name"] in wanted]
    missing = wanted - {window["name"] for window in selected}
    if missing:
        raise ValueError(f"Unknown windows: {', '.join(sorted(missing))}")
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description="Sweep BTC MA trend-gate freeze parameters.")
    parser.add_argument("--history-dir", type=Path, default=HISTORY_DIR)
    parser.add_argument("--windows", default=None, help="Comma-separated window names.")
    parser.add_argument("--freeze-days", default=",".join(str(x) for x in DEFAULT_FREEZE_DAYS))
    parser.add_argument("--fee-rate", type=float, default=0.001)
    parser.add_argument("--output", type=Path, default=Path("outputs/sweep_btc_ma.csv"))
    args = parser.parse_args()

    freeze_days = _parse_int_list(args.freeze_days)
    windows = _select_windows(args.windows)

    frames = []
    for window in windows:
        print(f"Window {window['name']} ({window['interval']}): {window['start']} -> {window['end']}")
        frame = run_sweep_for_window(args.history_dir, window, freeze_days, args.fee_rate)
        raw_flips = int(frame["raw_flips"].iloc[0]) if not frame.empty else 0
        print(f"  ma_period={frame['ma_period'].iloc[0]} raw_flips={raw_flips}")
        frames.append(frame)

    result = pd.concat(frames, ignore_index=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output, index=False)

    print()
    print(result.to_string(index=False))
    print(f"\nSaved: {args.output}")


if __name__ == "__main__":
    main()
