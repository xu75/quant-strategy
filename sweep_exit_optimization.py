#!/usr/bin/env python3
"""Sweep four exit/entry optimization mechanisms for BTC MA240 trend strategy.

Uses production signal semantics:
- Entry: crossover (prev_close <= prev_ma AND close > ma)
- Exit: close < ma after min_hold_bars
- Next-bar execution: signal at bar N close, execute at bar N+1 open
- Fee: 0.001 per side

Four experiment groups:
1. Exit Confirm: after min_hold, require N consecutive bars below MA to sell
2. Exit Buffer: after min_hold, require close < MA * (1 - buffer) to sell
3. Entry Confirm: require N consecutive bars above MA to enter (replaces crossover)
4. Symmetric Confirm: both entry and exit require N consecutive bars
"""

import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from sweep_btc_ma import HISTORY_DIR, load_btc_interval, ma_period_for_interval

WINDOWS = [
    {"name": "2y", "start": "2024-01-01", "end": "2026-04-30"},
    {"name": "3y", "start": "2023-01-01", "end": "2026-04-30"},
    {"name": "6y", "start": "2020-01-01", "end": "2026-04-30"},
]

FEE_RATE = 0.001
INTERVAL = "4h"
MA_PERIOD = 240


@dataclass
class BacktestResult:
    total_return: float
    max_dd: float
    trades: int
    buy_hold_return: float


def _parse_ts(value: str, tz: str = "UTC") -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize(tz) if ts.tzinfo is None else ts.tz_convert(tz)


def _end_exclusive(value: str, tz: str = "UTC") -> pd.Timestamp:
    ts = _parse_ts(value, tz)
    if len(value) == 10 and value.count("-") == 2:
        return ts + pd.Timedelta(days=1)
    return ts


def _slice(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    return df[(df.index >= _parse_ts(start)) & (df.index < _end_exclusive(end))].copy()


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    return float((equity / peak - 1.0).min())


def run_backtest(
    df: pd.DataFrame,
    min_hold_bars: int = 24,
    exit_confirm_bars: int = 0,
    exit_buffer: float = 0.0,
    entry_confirm_bars: int = 0,
    fee_rate: float = FEE_RATE,
) -> BacktestResult:
    """Run one backtest with production-like signal semantics.

    Parameters control which mechanism is active:
    - Baseline: all extras = 0
    - Exit Confirm: exit_confirm_bars > 0
    - Exit Buffer: exit_buffer > 0
    - Entry Confirm: entry_confirm_bars > 0
    - Symmetric: both entry_confirm_bars > 0 and exit_confirm_bars > 0
    """
    work = df.sort_index().copy()
    if len(work) < 2:
        return BacktestResult(0.0, 0.0, 0, 0.0)

    closes = work["close"].values
    opens = work["open"].values
    mas = work["ma"].values

    cash = 1.0
    units = 0.0
    in_position = False
    entry_bar = 0
    pending_buy = False
    pending_sell = False
    consecutive_below = 0
    consecutive_above = 0
    trades = 0
    equity_list = []

    for i in range(len(work)):
        price = closes[i]
        ma = mas[i]
        open_price = opens[i]

        if pd.isna(ma):
            equity_list.append(cash if not in_position else units * price * (1 - fee_rate))
            continue

        # Execute pending orders at this bar's open
        if pending_buy and not in_position:
            units = cash / (open_price * (1 + fee_rate))
            cash = 0.0
            in_position = True
            entry_bar = i
            trades += 1
            pending_buy = False
            consecutive_above = 0

        if pending_sell and in_position:
            cash = units * open_price * (1 - fee_rate)
            units = 0.0
            in_position = False
            trades += 1
            pending_sell = False
            consecutive_below = 0

        # Signal evaluation at this bar's close
        if not in_position:
            if entry_confirm_bars > 0:
                # Entry Confirm: N consecutive bars above MA
                if price > ma:
                    consecutive_above += 1
                    if consecutive_above >= entry_confirm_bars:
                        pending_buy = True
                else:
                    consecutive_above = 0
            else:
                # Production crossover entry
                if i > 0 and not pd.isna(mas[i - 1]):
                    prev_price = closes[i - 1]
                    prev_ma = mas[i - 1]
                    if prev_price <= prev_ma and price > ma:
                        pending_buy = True
        else:
            hold_bars = i - entry_bar
            if hold_bars < min_hold_bars:
                continue

            if exit_buffer > 0:
                # Exit Buffer: close < MA * (1 - buffer)
                if price < ma * (1 - exit_buffer):
                    pending_sell = True
                    consecutive_below = 0
            elif exit_confirm_bars > 0:
                # Exit Confirm: N consecutive bars below MA
                if price < ma:
                    consecutive_below += 1
                    if consecutive_below >= exit_confirm_bars:
                        pending_sell = True
                else:
                    consecutive_below = 0
            else:
                # Baseline: close < MA
                if price < ma:
                    pending_sell = True

        equity = cash if not in_position else units * price * (1 - fee_rate)
        equity_list.append(equity)

    equity_series = pd.Series(equity_list)
    total_return = float(equity_series.iloc[-1] / 1.0 - 1.0) if len(equity_series) > 0 else 0.0
    max_dd = _max_drawdown(equity_series)
    bh_return = float(closes[-1] / opens[0] - 1.0) if len(work) > 0 else 0.0

    return BacktestResult(total_return, max_dd, trades, bh_return)


def main():
    full = load_btc_interval(HISTORY_DIR, INTERVAL, "UTC")
    full = full.copy()
    full["ma"] = full["close"].rolling(window=MA_PERIOD, min_periods=MA_PERIOD).mean()

    results = []

    for window in WINDOWS:
        wdf = _slice(full, window["start"], window["end"]).dropna(subset=["ma"])
        print(f"\n=== {window['name']}: {len(wdf)} bars ===")

        # --- Baseline ---
        for mh in [12, 18, 24]:
            r = run_backtest(wdf, min_hold_bars=mh)
            results.append({
                "window": window["name"], "group": "baseline",
                "min_hold": mh, "param_name": "none", "param_value": 0,
                "return": r.total_return, "max_dd": r.max_dd,
                "trades": r.trades, "buy_hold": r.buy_hold_return,
            })
            print(f"  baseline mh={mh}: ret={r.total_return:.2%} dd={r.max_dd:.2%} trades={r.trades}")

        # --- Group 1: Exit Confirm ---
        for ecb in [1, 2, 3, 5]:
            for mh in [12, 18, 24]:
                r = run_backtest(wdf, min_hold_bars=mh, exit_confirm_bars=ecb)
                results.append({
                    "window": window["name"], "group": "exit_confirm",
                    "min_hold": mh, "param_name": "exit_confirm_bars", "param_value": ecb,
                    "return": r.total_return, "max_dd": r.max_dd,
                    "trades": r.trades, "buy_hold": r.buy_hold_return,
                })
                print(f"  exit_confirm ecb={ecb} mh={mh}: ret={r.total_return:.2%} dd={r.max_dd:.2%} trades={r.trades}")

        # --- Group 2: Exit Buffer ---
        for buf in [0.005, 0.01, 0.015, 0.02]:
            for mh in [12, 18, 24]:
                r = run_backtest(wdf, min_hold_bars=mh, exit_buffer=buf)
                results.append({
                    "window": window["name"], "group": "exit_buffer",
                    "min_hold": mh, "param_name": "exit_buffer_pct", "param_value": buf,
                    "return": r.total_return, "max_dd": r.max_dd,
                    "trades": r.trades, "buy_hold": r.buy_hold_return,
                })
                print(f"  exit_buffer buf={buf:.1%} mh={mh}: ret={r.total_return:.2%} dd={r.max_dd:.2%} trades={r.trades}")

        # --- Group 3: Entry Confirm ---
        for ecb in [1, 2, 3]:
            for mh in [18, 24]:
                r = run_backtest(wdf, min_hold_bars=mh, entry_confirm_bars=ecb)
                results.append({
                    "window": window["name"], "group": "entry_confirm",
                    "min_hold": mh, "param_name": "entry_confirm_bars", "param_value": ecb,
                    "return": r.total_return, "max_dd": r.max_dd,
                    "trades": r.trades, "buy_hold": r.buy_hold_return,
                })
                print(f"  entry_confirm ecb={ecb} mh={mh}: ret={r.total_return:.2%} dd={r.max_dd:.2%} trades={r.trades}")

        # --- Group 4: Symmetric Confirm ---
        for cb in [1, 2, 3]:
            for mh in [12, 18, 24]:
                r = run_backtest(wdf, min_hold_bars=mh, entry_confirm_bars=cb, exit_confirm_bars=cb)
                results.append({
                    "window": window["name"], "group": "symmetric_confirm",
                    "min_hold": mh, "param_name": "confirm_bars", "param_value": cb,
                    "return": r.total_return, "max_dd": r.max_dd,
                    "trades": r.trades, "buy_hold": r.buy_hold_return,
                })
                print(f"  symmetric cb={cb} mh={mh}: ret={r.total_return:.2%} dd={r.max_dd:.2%} trades={r.trades}")

    df = pd.DataFrame(results)
    output = Path("outputs/sweep_exit_optimization.csv")
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output, index=False)
    print(f"\nSaved: {output}")

    # Summary: best per group by mean return across windows
    print("\n=== Best per group (by mean return across windows) ===")
    pivot = df.pivot_table(
        index=["group", "min_hold", "param_name", "param_value"],
        columns="window",
        values="return",
    )
    pivot["mean_return"] = pivot.mean(axis=1)
    pivot["min_return"] = pivot.min(axis=1)

    for group in ["baseline", "exit_confirm", "exit_buffer", "entry_confirm", "symmetric_confirm"]:
        grp = pivot.loc[pivot.index.get_level_values("group") == group]
        if grp.empty:
            continue
        top = grp.sort_values("mean_return", ascending=False).head(3)
        print(f"\n--- {group} top 3 ---")
        print(top.to_string())


if __name__ == "__main__":
    main()
