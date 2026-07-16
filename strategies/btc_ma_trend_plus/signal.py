from __future__ import annotations

"""BTC MA Trend Strategy Plus - Signal Logic

TrendLock 40 Plus: exit_confirm optimization over the base TrendLock 40.

Strategy rules:
- Timeframe: 4H candles
- Trend filter: MA(window) on close prices
- Entry: close crosses above MA (same as TrendLock 40)
- Entry slope gate (effective 2026-07): at the golden-cross bar, block entry
  if MA is falling steeply. slope = MA(now) / MA(slope_lookback_bars ago) - 1;
  if slope < slope_gate_threshold the cross is skipped (wait for next cross).
  Only gates NEW long entries; exits and open positions are untouched.
- Exit: after min_hold, require exit_confirm_bars consecutive bars
  with close < MA before selling
- No stop loss, no take profit, no pyramiding
"""

from dataclasses import dataclass

import pandas as pd


@dataclass
class StrategyConfig:
    """Configuration for the MA trend strategy with exit confirmation."""

    display_name: str = "TrendLock 40 Plus"
    internal_code: str = "T40P-4"
    ma_window: int = 240
    min_hold_bars: int = 12
    exit_confirm_bars: int = 2
    timeframe: str = "4H"
    symbol: str = "BTC-USDT"
    data_source: str = "okx"
    # Entry slope gate (MA240 开多斜率门, effective 2026-07).
    # At the golden-cross bar, block entry if MA slope over slope_lookback_bars
    # is below slope_gate_threshold. Only affects new long entries.
    slope_gate_enabled: bool = True
    slope_lookback_bars: int = 30
    slope_gate_threshold: float = -0.02
    # ISO date (UTC). Crosses strictly before this date are never gated, so the
    # historical backtest stays intact and the rule only takes effect forward.
    # Empty string = apply the gate over all history (counterfactual mode).
    slope_gate_effective_date: str = "2026-07-01"


@dataclass
class Signal:
    """A trading signal produced by the strategy."""

    action: str  # "buy", "sell", "hold"
    price: float
    ma_value: float
    timestamp: pd.Timestamp
    hold_bars: int = 0
    reason: str = ""


def _parse_effective_date(effective_date: str) -> pd.Timestamp | None:
    """Parse the slope-gate effective date to a tz-aware UTC Timestamp.

    Returns None when empty (gate applies over all history / counterfactual).
    """
    if not effective_date:
        return None
    ts = pd.Timestamp(effective_date)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts


def _compute_ma_slope(ma_series: pd.Series, idx: int, lookback: int) -> float | None:
    """MA slope over `lookback` bars ending at idx: ma[idx]/ma[idx-lookback]-1.

    Returns None when history is insufficient or the reference MA is NaN/zero,
    signalling the caller to pass the entry through (no gating).
    """
    ref_idx = idx - lookback
    if ref_idx < 0:
        return None
    ma_now = ma_series.iloc[idx]
    ma_ref = ma_series.iloc[ref_idx]
    if pd.isna(ma_now) or pd.isna(ma_ref) or ma_ref == 0:
        return None
    return float(ma_now / ma_ref - 1.0)


def _slope_gate_blocks(
    ma_series: pd.Series,
    idx: int,
    cross_ts: pd.Timestamp,
    config: StrategyConfig,
    effective_ts: pd.Timestamp | None,
) -> tuple[bool, float | None]:
    """Decide whether the slope gate blocks a golden cross at bar `idx`.

    Returns (blocked, slope). `blocked` is True only when the gate is enabled,
    the cross is on/after the effective date, MA history is sufficient, and the
    slope is below threshold. When history is insufficient the entry passes
    through (blocked=False, slope=None).
    """
    if not config.slope_gate_enabled:
        return False, None

    # Forward-only activation: crosses before the effective date pass through,
    # so the historical backtest is unchanged and the rule only takes effect
    # from slope_gate_effective_date onward.
    if effective_ts is not None:
        cross_cmp = cross_ts
        if getattr(cross_cmp, "tzinfo", None) is None:
            cross_cmp = pd.Timestamp(cross_cmp).tz_localize("UTC")
        if cross_cmp < effective_ts:
            return False, None

    slope = _compute_ma_slope(ma_series, idx, config.slope_lookback_bars)
    if slope is None:
        return False, None
    if slope < config.slope_gate_threshold:
        return True, slope
    return False, slope


def compute_signals(df: pd.DataFrame, config: StrategyConfig | None = None, **kwargs) -> list[Signal]:
    if config is None:
        config = StrategyConfig()

    if len(df) < config.ma_window + 1:
        return []

    df = df.copy()
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["ma"] = df["close"].rolling(window=config.ma_window, min_periods=config.ma_window).mean()
    ma_series = df["ma"]
    effective_ts = _parse_effective_date(config.slope_gate_effective_date)

    signals: list[Signal] = []
    in_position = False
    entry_bar_idx = 0
    consecutive_below = 0

    for i in range(config.ma_window, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]

        price = row["close"]
        ma = row["ma"]
        prev_price = prev["close"]
        prev_ma = prev["ma"]

        if pd.isna(ma) or pd.isna(prev_ma):
            continue

        if not in_position:
            if prev_price <= prev_ma and price > ma:
                blocked, slope = _slope_gate_blocks(
                    ma_series, i, row["timestamp"], config, effective_ts
                )
                if blocked:
                    # Golden cross rejected by the slope gate. No delayed
                    # re-entry: simply wait for the next crossover.
                    continue
                slope_note = "" if slope is None else f", MA slope {slope * 100:+.2f}%"
                signals.append(Signal(
                    action="buy",
                    price=price,
                    ma_value=ma,
                    timestamp=row["timestamp"],
                    reason=f"Close crossed above MA{config.ma_window}{slope_note}",
                ))
                in_position = True
                entry_bar_idx = i
                consecutive_below = 0
        else:
            hold_bars = i - entry_bar_idx
            if hold_bars < config.min_hold_bars:
                continue

            if price < ma:
                consecutive_below += 1
                if consecutive_below >= config.exit_confirm_bars:
                    signals.append(Signal(
                        action="sell",
                        price=price,
                        ma_value=ma,
                        timestamp=row["timestamp"],
                        hold_bars=hold_bars,
                        reason=f"Close below MA{config.ma_window} for {consecutive_below} consecutive bars after {hold_bars} bars",
                    ))
                    in_position = False
                    consecutive_below = 0
            else:
                consecutive_below = 0

    return signals


def get_current_signal(df: pd.DataFrame, in_position: bool,
                       entry_bar_idx: int = 0,
                       config: StrategyConfig | None = None, **kwargs) -> Signal:
    if config is None:
        config = StrategyConfig()

    df = df.copy()
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["ma"] = df["close"].rolling(window=config.ma_window, min_periods=config.ma_window).mean()

    last = df.iloc[-1]
    prev = df.iloc[-2]
    price = last["close"]
    ma = last["ma"]

    if pd.isna(ma):
        return Signal(
            action="hold",
            price=price,
            ma_value=0.0,
            timestamp=last["timestamp"],
            reason="Insufficient data for MA",
        )

    if not in_position:
        prev_price = prev["close"]
        prev_ma = prev["ma"]
        if not pd.isna(prev_ma) and prev_price <= prev_ma and price > ma:
            effective_ts = _parse_effective_date(config.slope_gate_effective_date)
            blocked, slope = _slope_gate_blocks(
                df["ma"], len(df) - 1, last["timestamp"], config, effective_ts
            )
            if blocked:
                return Signal(
                    action="hold",
                    price=price,
                    ma_value=ma,
                    timestamp=last["timestamp"],
                    reason=(
                        f"Golden cross blocked by slope gate: MA{config.ma_window} "
                        f"slope {slope * 100:+.2f}% < {config.slope_gate_threshold * 100:+.1f}% "
                        f"(waiting for next cross)"
                    ),
                )
            slope_note = "" if slope is None else f", MA slope {slope * 100:+.2f}%"
            return Signal(
                action="buy",
                price=price,
                ma_value=ma,
                timestamp=last["timestamp"],
                reason=f"Close crossed above MA{config.ma_window}{slope_note}",
            )
        position_str = "above" if price > ma else "below"
        return Signal(
            action="hold",
            price=price,
            ma_value=ma,
            timestamp=last["timestamp"],
            reason=f"No position, price {position_str} MA{config.ma_window}",
        )

    hold_bars = len(df) - 1 - entry_bar_idx
    if hold_bars < config.min_hold_bars:
        return Signal(
            action="hold",
            price=price,
            ma_value=ma,
            timestamp=last["timestamp"],
            hold_bars=hold_bars,
            reason=f"Holding {hold_bars}/{config.min_hold_bars} bars, min hold not met",
        )

    # Count consecutive bars below MA from the tail
    consecutive_below = 0
    for i in range(len(df) - 1, max(entry_bar_idx + config.min_hold_bars - 1, -1), -1):
        row_price = df.iloc[i]["close"]
        row_ma = df.iloc[i]["ma"]
        if pd.isna(row_ma):
            break
        if row_price < row_ma:
            consecutive_below += 1
        else:
            break

    if consecutive_below >= config.exit_confirm_bars:
        return Signal(
            action="sell",
            price=price,
            ma_value=ma,
            timestamp=last["timestamp"],
            hold_bars=hold_bars,
            reason=f"Close below MA{config.ma_window} for {consecutive_below} consecutive bars after {hold_bars} bars",
        )

    return Signal(
        action="hold",
        price=price,
        ma_value=ma,
        timestamp=last["timestamp"],
        hold_bars=hold_bars,
        reason=f"Holding, price {'below' if price < ma else 'above'} MA{config.ma_window} ({consecutive_below}/{config.exit_confirm_bars} exit confirm)",
    )
