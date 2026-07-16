#!/usr/bin/env python3
"""Verify the TrendLock 40 Plus MA240 slope gate (开多斜率门).

Runs three configs on identical production 4H data / fee model:
  1. baseline        — slope gate disabled (current production)
  2. forward-only    — gate effective 2026-07-01 (new production default)
  3. counterfactual  — gate applied over all history (validates the research claim)

For the counterfactual, also classifies every historical golden cross by its
MA240 slope to reproduce the "30 crosses with slope < -2%, 13% win rate" claim.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.backtest import run_backtest  # noqa: E402
from pipeline.data_fetcher import load_local_history_by_name  # noqa: E402
from strategies.btc_ma_trend_plus.signal import (  # noqa: E402
    StrategyConfig,
    compute_signals,
    _compute_ma_slope,
    _parse_effective_date,
)

FEE = 0.001


def fmt(result) -> str:
    return (
        f"trades={result.total_trades:3d}  "
        f"return={result.total_return_pct:9.2f}%  "
        f"maxDD={result.max_drawdown_pct:6.2f}%  "
        f"win={result.win_rate:5.2f}%  "
        f"sharpe={result.sharpe_ratio:.3f}"
    )


def bucket_trades_by_entry_slope(df: pd.DataFrame, config: StrategyConfig) -> None:
    """Split BASELINE trades by MA slope at entry, testing the 13%/31% claim.

    Reproduces each round trip from gate-off signals, computes the MA slope at
    the entry bar, and reports win rate + net PnL for slope<threshold vs rest.
    """
    d = df.sort_values("timestamp").reset_index(drop=True).copy()
    d["ma"] = d["close"].rolling(config.ma_window, min_periods=config.ma_window).mean()
    ma = d["ma"]
    idx_by_ts = {ts: i for i, ts in enumerate(d["timestamp"])}

    off = replace(config, slope_gate_enabled=False)
    sigs = compute_signals(d, off)
    # signals strictly alternate buy/sell (toggle on in_position)
    trades = []
    pending_buy = None
    for s in sigs:
        if s.action == "buy":
            pending_buy = s
        elif s.action == "sell" and pending_buy is not None:
            entry_idx = idx_by_ts[pending_buy.timestamp]
            slope = _compute_ma_slope(ma, entry_idx, config.slope_lookback_bars)
            ret = (s.price * (1 - FEE) - pending_buy.price * (1 + FEE)) / (
                pending_buy.price * (1 + FEE)
            )
            trades.append({"slope": slope, "ret": ret})
            pending_buy = None

    t = pd.DataFrame(trades).dropna(subset=["slope"])
    steep = t[t["slope"] < config.slope_gate_threshold]
    rest = t[t["slope"] >= config.slope_gate_threshold]

    def line(label, g):
        if len(g) == 0:
            print(f"  {label:28s} n=0")
            return
        win = (g["ret"] > 0).mean() * 100
        net = g["ret"].sum() * 100
        print(f"  {label:28s} n={len(g):3d}  win={win:5.1f}%  net_pnl_sum={net:8.1f}%")

    print("\n--- Baseline trades bucketed by MA slope at ENTRY (tests 13%/31% claim) ---")
    print(f"  (round trips with sufficient MA history: {len(t)})")
    line(f"slope <  {config.slope_gate_threshold*100:.0f}% (would be blocked)", steep)
    line(f"slope >= {config.slope_gate_threshold*100:.0f}% (kept)", rest)


def run(df: pd.DataFrame, config: StrategyConfig):
    signals = compute_signals(df, config)
    return run_backtest(df, config, signals=signals, fee_rate=FEE), signals


def classify_crosses(df: pd.DataFrame, config: StrategyConfig) -> pd.DataFrame:
    """Label every golden cross with its MA slope and next-cross forward outcome."""
    d = df.sort_values("timestamp").reset_index(drop=True).copy()
    d["ma"] = d["close"].rolling(config.ma_window, min_periods=config.ma_window).mean()
    ma = d["ma"]
    rows = []
    for i in range(config.ma_window, len(d)):
        if pd.isna(ma.iloc[i]) or pd.isna(ma.iloc[i - 1]):
            continue
        if d["close"].iloc[i - 1] <= ma.iloc[i - 1] and d["close"].iloc[i] > ma.iloc[i]:
            slope = _compute_ma_slope(ma, i, config.slope_lookback_bars)
            rows.append({"idx": i, "timestamp": d["timestamp"].iloc[i],
                         "price": d["close"].iloc[i], "slope": slope})
    return pd.DataFrame(rows)


def main() -> None:
    df = load_local_history_by_name("BTC-USD_1h.csv", target_bar="4H", canonical_only=True)
    print(f"Data: {len(df)} 4H candles, {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}\n")

    base_cfg = StrategyConfig()  # manifest defaults (gate on, effective 2026-07-01)

    configs = {
        "1. baseline (gate off)": replace(base_cfg, slope_gate_enabled=False),
        "2. forward-only (2026-07-01)": base_cfg,
        "3. counterfactual (all history)": replace(base_cfg, slope_gate_effective_date=""),
    }
    results = {}
    for name, cfg in configs.items():
        res, sigs = run(df, cfg)
        results[name] = res
        buys = sum(1 for s in sigs if s.action == "buy")
        print(f"{name:34s}  {fmt(res)}  (buys={buys})")

    # --- Cross classification against the research claim ---
    print("\n--- Golden-cross slope classification (counterfactual view) ---")
    crosses = classify_crosses(df, base_cfg)
    have_slope = crosses.dropna(subset=["slope"])
    steep = have_slope[have_slope["slope"] < base_cfg.slope_gate_threshold]
    ok = have_slope[have_slope["slope"] >= base_cfg.slope_gate_threshold]
    print(f"Total golden crosses (MA history sufficient): {len(have_slope)}")
    print(f"  slope <  {base_cfg.slope_gate_threshold*100:.0f}% (BLOCKED): {len(steep)}")
    print(f"  slope >= {base_cfg.slope_gate_threshold*100:.0f}% (kept)   : {len(ok)}")

    # Forward-only: how many crosses fall on/after the effective date?
    eff = _parse_effective_date(base_cfg.slope_gate_effective_date)
    fwd = crosses[crosses["timestamp"] >= eff]
    fwd_blocked = fwd.dropna(subset=["slope"])
    fwd_blocked = fwd_blocked[fwd_blocked["slope"] < base_cfg.slope_gate_threshold]
    print(f"\nCrosses on/after {eff.date()} (forward-only scope): {len(fwd)}"
          f"  -> blocked by gate: {len(fwd_blocked)}")

    bucket_trades_by_entry_slope(df, base_cfg)


if __name__ == "__main__":
    main()
