#!/usr/bin/env python3
"""Pre-launch validation for TrendLock 40 Plus.

Three validation items:
1. Trade-level diff: compare Plus vs baseline trades
2. Segment analysis: bull / bear / sideways performance
3. Slippage stress test: fee=0.1% / 0.2% / 0.3%
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from sweep_btc_ma import HISTORY_DIR, load_btc_interval
from sweep_exit_optimization import run_backtest, BacktestResult, INTERVAL, MA_PERIOD, FEE_RATE

WINDOWS = [
    {"name": "6y", "start": "2020-01-01", "end": "2026-04-30"},
]

SEGMENTS = [
    {"name": "bull_2020-2021", "start": "2020-10-01", "end": "2021-11-15"},
    {"name": "bear_2021-2022", "start": "2021-11-15", "end": "2022-11-15"},
    {"name": "sideways_2023-2024", "start": "2023-01-01", "end": "2024-12-31"},
    {"name": "recent_2025", "start": "2025-01-01", "end": "2026-04-30"},
]


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


def load_data() -> pd.DataFrame:
    full = load_btc_interval(HISTORY_DIR, INTERVAL, "UTC")
    full = full.copy()
    full["ma"] = full["close"].rolling(window=MA_PERIOD, min_periods=MA_PERIOD).mean()
    return full


def collect_trades(
    df: pd.DataFrame,
    min_hold_bars: int = 24,
    exit_confirm_bars: int = 0,
    fee_rate: float = FEE_RATE,
) -> list[dict]:
    """Replay sweep backtest logic and record each trade with timestamps."""
    work = df.sort_index().copy()
    closes = work["close"].values
    opens = work["open"].values
    mas = work["ma"].values
    timestamps = work.index.tolist()

    trades = []
    cash = 1.0
    units = 0.0
    in_position = False
    entry_bar = 0
    entry_ts = None
    entry_price = 0.0
    pending_buy = False
    pending_sell = False
    consecutive_below = 0

    for i in range(len(work)):
        price = closes[i]
        ma = mas[i]
        open_price = opens[i]

        if pd.isna(ma):
            continue

        if pending_buy and not in_position:
            entry_price = open_price * (1 + fee_rate)
            units = cash / entry_price
            cash = 0.0
            in_position = True
            entry_bar = i
            entry_ts = timestamps[i]
            pending_buy = False

        if pending_sell and in_position:
            exit_price = open_price * (1 - fee_rate)
            pnl_pct = (exit_price / entry_price - 1) * 100
            trades.append({
                "entry_time": str(entry_ts),
                "entry_price": round(opens[entry_bar], 2),
                "exit_time": str(timestamps[i]),
                "exit_price": round(open_price, 2),
                "hold_bars": i - entry_bar,
                "pnl_pct": round(pnl_pct, 2),
            })
            cash = units * exit_price
            units = 0.0
            in_position = False
            pending_sell = False
            consecutive_below = 0

        if not in_position:
            if i > 0 and not pd.isna(mas[i - 1]):
                if closes[i - 1] <= mas[i - 1] and price > ma:
                    pending_buy = True
        else:
            hold_bars = i - entry_bar
            if hold_bars < min_hold_bars:
                continue

            if exit_confirm_bars > 0:
                if price < ma:
                    consecutive_below += 1
                    if consecutive_below >= exit_confirm_bars:
                        pending_sell = True
                else:
                    consecutive_below = 0
            else:
                if price < ma:
                    pending_sell = True

    return trades


def run_trade_level_diff(full: pd.DataFrame):
    print("\n" + "=" * 60)
    print("1. TRADE-LEVEL DIFF: Plus vs Baseline (6y)")
    print("=" * 60)

    wdf = _slice(full, "2020-01-01", "2026-04-30").dropna(subset=["ma"])

    baseline = run_backtest(wdf, min_hold_bars=24)
    plus = run_backtest(wdf, min_hold_bars=12, exit_confirm_bars=2)

    print(f"\nBaseline (mh=24):     ret={baseline.total_return:.2%}  dd={baseline.max_dd:.2%}  trades={baseline.trades}")
    print(f"Plus (ecb=2, mh=12):  ret={plus.total_return:.2%}  dd={plus.max_dd:.2%}  trades={plus.trades}")
    print(f"\nDelta: ret={plus.total_return - baseline.total_return:+.2%}  dd={plus.max_dd - baseline.max_dd:+.2%}  trades={plus.trades - baseline.trades:+d}")

    baseline_trades = collect_trades(wdf, min_hold_bars=24)
    plus_trades = collect_trades(wdf, min_hold_bars=12, exit_confirm_bars=2)

    print(f"\nTrade lists: {len(baseline_trades)} baseline, {len(plus_trades)} plus")

    # Phase 1: Show shared-entry trades (before paths diverge)
    # Both strategies share the same crossover entry logic, so early trades
    # have identical entries. After a different exit, subsequent entries diverge.
    print(f"\n--- Shared-entry trades (same entry timestamp) ---")
    print(f"{'#':>3}  {'BL Exit':<22}  {'Plus Exit':<22}  {'BL Hold':>7}  {'Plus Hold':>8}  {'BL P&L':>8}  {'Plus P&L':>8}  {'Delta':>8}  Note")
    print("-" * 120)

    bl_by_entry = {t["entry_time"]: t for t in baseline_trades}
    plus_by_entry = {t["entry_time"]: t for t in plus_trades}
    shared_entries = sorted(set(bl_by_entry) & set(plus_by_entry))

    shared_delta_sum = 0.0
    delayed_count = 0
    earlier_count = 0
    same_count = 0
    for i, entry in enumerate(shared_entries):
        bt = bl_by_entry[entry]
        pt = plus_by_entry[entry]
        delta = pt["pnl_pct"] - bt["pnl_pct"]
        shared_delta_sum += delta
        if bt["exit_time"] == pt["exit_time"]:
            note = "same exit"
            same_count += 1
        elif bt["exit_time"] < pt["exit_time"]:
            note = f"Plus delayed +{pt['hold_bars'] - bt['hold_bars']}bars (exit_confirm)"
            delayed_count += 1
        else:
            note = f"Plus earlier -{bt['hold_bars'] - pt['hold_bars']}bars (shorter min_hold)"
            earlier_count += 1
        print(f"{i+1:>3}  {bt['exit_time'][:19]:<22}  {pt['exit_time'][:19]:<22}  {bt['hold_bars']:>6}b  {pt['hold_bars']:>7}b  {bt['pnl_pct']:>+7.2f}%  {pt['pnl_pct']:>+7.2f}%  {delta:>+7.2f}%  {note}")

    print(f"\nShared-entry summary: {len(shared_entries)} trades")
    print(f"  Same exit: {same_count}  |  Plus delayed (ecb): {delayed_count}  |  Plus earlier (shorter mh): {earlier_count}")
    print(f"  Cumulative P&L delta on shared trades: {shared_delta_sum:+.2f}pp")

    # Phase 2: Show divergent trades (entries unique to one strategy)
    bl_only_entries = sorted(set(bl_by_entry) - set(plus_by_entry))
    plus_only_entries = sorted(set(plus_by_entry) - set(bl_by_entry))

    if bl_only_entries or plus_only_entries:
        print(f"\n--- Divergent trades (path split after different exit timing) ---")
        bl_only_pnl = sum(bl_by_entry[e]["pnl_pct"] for e in bl_only_entries)
        plus_only_pnl = sum(plus_by_entry[e]["pnl_pct"] for e in plus_only_entries)
        print(f"  Baseline-only: {len(bl_only_entries)} trades, total P&L {bl_only_pnl:+.2f}%")
        for e in bl_only_entries:
            t = bl_by_entry[e]
            print(f"    {t['entry_time'][:19]} → {t['exit_time'][:19]}  hold={t['hold_bars']}b  P&L={t['pnl_pct']:+.2f}%")
        print(f"  Plus-only: {len(plus_only_entries)} trades, total P&L {plus_only_pnl:+.2f}%")
        for e in plus_only_entries:
            t = plus_by_entry[e]
            print(f"    {t['entry_time'][:19]} → {t['exit_time'][:19]}  hold={t['hold_bars']}b  P&L={t['pnl_pct']:+.2f}%")

    return baseline, plus


def run_segment_analysis(full: pd.DataFrame):
    print("\n" + "=" * 60)
    print("2. SEGMENT ANALYSIS: Bull / Bear / Sideways / Recent")
    print("=" * 60)

    rows = []
    for seg in SEGMENTS:
        wdf = _slice(full, seg["start"], seg["end"]).dropna(subset=["ma"])
        if len(wdf) < 10:
            print(f"  {seg['name']}: insufficient data ({len(wdf)} bars)")
            continue

        baseline = run_backtest(wdf, min_hold_bars=24)
        plus = run_backtest(wdf, min_hold_bars=12, exit_confirm_bars=2)

        rows.append({
            "segment": seg["name"],
            "baseline_ret": f"{baseline.total_return:.2%}",
            "plus_ret": f"{plus.total_return:.2%}",
            "delta_ret": f"{plus.total_return - baseline.total_return:+.2%}",
            "baseline_dd": f"{baseline.max_dd:.2%}",
            "plus_dd": f"{plus.max_dd:.2%}",
            "baseline_trades": baseline.trades,
            "plus_trades": plus.trades,
        })

        winner = "Plus" if plus.total_return > baseline.total_return else "Baseline"
        print(f"\n  {seg['name']}:")
        print(f"    Baseline: ret={baseline.total_return:.2%}  dd={baseline.max_dd:.2%}  trades={baseline.trades}")
        print(f"    Plus:     ret={plus.total_return:.2%}  dd={plus.max_dd:.2%}  trades={plus.trades}")
        print(f"    Winner:   {winner} ({plus.total_return - baseline.total_return:+.2%})")

    return rows


def run_slippage_stress_test(full: pd.DataFrame):
    print("\n" + "=" * 60)
    print("3. SLIPPAGE STRESS TEST: fee=0.1% / 0.2% / 0.3%")
    print("=" * 60)

    wdf = _slice(full, "2020-01-01", "2026-04-30").dropna(subset=["ma"])

    for fee in [0.001, 0.002, 0.003]:
        baseline = run_backtest(wdf, min_hold_bars=24, fee_rate=fee)
        plus = run_backtest(wdf, min_hold_bars=12, exit_confirm_bars=2, fee_rate=fee)

        plus_wins = plus.total_return > baseline.total_return
        print(f"\n  fee={fee:.1%}:")
        print(f"    Baseline: ret={baseline.total_return:.2%}  dd={baseline.max_dd:.2%}")
        print(f"    Plus:     ret={plus.total_return:.2%}  dd={plus.max_dd:.2%}")
        print(f"    Plus wins: {'YES' if plus_wins else 'NO'} ({plus.total_return - baseline.total_return:+.2%})")


def main():
    print("TrendLock 40 Plus Pre-Launch Validation")
    print("Config: exit_confirm_bars=2, min_hold_bars=12, MA240, 4H")

    full = load_data()
    print(f"Loaded {len(full)} bars, {full.index[0]} to {full.index[-1]}")

    run_trade_level_diff(full)
    run_segment_analysis(full)
    run_slippage_stress_test(full)

    print("\n" + "=" * 60)
    print("VALIDATION COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
