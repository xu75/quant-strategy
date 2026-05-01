"""BTC MA Trend Strategy - Signal Logic

Extracted from the live 4H_MA120_4D_Trader bot.
Pure signal logic with no exchange interaction.

Strategy rules:
- Timeframe: 4H candles
- Trend filter: MA(window) on close prices
- Entry: close crosses above MA (prev_close <= prev_ma AND current_close > current_ma)
- Exit: close < MA, but only after minimum hold period
- No stop loss, no take profit, no pyramiding
"""

from dataclasses import dataclass

import pandas as pd


@dataclass
class StrategyConfig:
    """Configuration for the MA trend strategy."""

    display_name: str = "TrendLock 40"
    internal_code: str = "T40-4"
    ma_window: int = 240
    min_hold_bars: int = 24  # 24 * 4H = 4 days
    timeframe: str = "4H"
    symbol: str = "BTC-USDT"


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
    """Compute trading signals from OHLCV data.

    Args:
        df: DataFrame with columns ['timestamp', 'close'], sorted oldest-first.
        config: Strategy configuration. Uses defaults if None.

    Returns:
        List of Signal objects for each bar where a trade action occurs.
    """
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
            # Buy: crossover confirmation
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
        else:
            # Sell: price below MA after minimum hold
            hold_bars = i - entry_bar_idx
            if hold_bars < config.min_hold_bars:
                continue

            if price < ma:
                signals.append(Signal(
                    action="sell",
                    price=price,
                    ma_value=ma,
                    timestamp=row["timestamp"],
                    hold_bars=hold_bars,
                    reason=f"Close below MA{config.ma_window} after {hold_bars} bars",
                ))
                in_position = False

    return signals


def get_current_signal(df: pd.DataFrame, in_position: bool,
                       entry_bar_idx: int = 0,
                       config: StrategyConfig | None = None) -> Signal:
    """Get the signal for the latest bar only.

    Used for live strategy status display.

    Args:
        df: DataFrame with ['timestamp', 'close'], sorted oldest-first.
        in_position: Whether currently holding a position.
        entry_bar_idx: Bar index when position was entered.
        config: Strategy configuration.

    Returns:
        Signal for the current bar.
    """
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

    if price < ma:
        return Signal(
            action="sell",
            price=price,
            ma_value=ma,
            timestamp=last["timestamp"],
            hold_bars=hold_bars,
            reason=f"Close below MA{config.ma_window} after {hold_bars} bars",
        )

    return Signal(
        action="hold",
        price=price,
        ma_value=ma,
        timestamp=last["timestamp"],
        hold_bars=hold_bars,
        reason=f"Holding, price above MA{config.ma_window}",
    )
