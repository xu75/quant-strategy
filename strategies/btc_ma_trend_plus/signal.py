from __future__ import annotations

"""BTC MA Trend Strategy Plus - Signal Logic

TrendLock 40 Plus: exit_confirm optimization over the base TrendLock 40.

Strategy rules:
- Timeframe: 4H candles
- Trend filter: MA(window) on close prices
- Entry: close crosses above MA (same as TrendLock 40)
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


@dataclass
class Signal:
    """A trading signal produced by the strategy."""

    action: str  # "buy", "sell", "hold"
    price: float
    ma_value: float
    timestamp: pd.Timestamp
    hold_bars: int = 0
    reason: str = ""


def compute_signals(df: pd.DataFrame, config: StrategyConfig | None = None) -> list[Signal]:
    if config is None:
        config = StrategyConfig()

    if len(df) < config.ma_window + 1:
        return []

    df = df.copy()
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["ma"] = df["close"].rolling(window=config.ma_window, min_periods=config.ma_window).mean()

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
                signals.append(Signal(
                    action="buy",
                    price=price,
                    ma_value=ma,
                    timestamp=row["timestamp"],
                    reason=f"Close crossed above MA{config.ma_window}",
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
                       config: StrategyConfig | None = None) -> Signal:
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
            return Signal(
                action="buy",
                price=price,
                ma_value=ma,
                timestamp=last["timestamp"],
                reason=f"Close crossed above MA{config.ma_window}",
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
