from __future__ import annotations

"""EchoTrend 240 - Signal Logic

Dual-feed strategy: BTC 4H SMA240 regime gate + MSTR regular-hours execution.

Regime gate:
- Resample BTC 1H → 4H, compute SMA240
- Bull: close > SMA240 for bull_confirm_bars consecutive 4H bars
- Bear: close < SMA240 for bear_confirm_bars consecutive 4H bars
- Shift(1) to avoid lookahead bias (signal available after 4H bar closes)

Execution:
- Bull regime → buy MSTR at next regular-hours bar open
- Bear regime → sell MSTR at next regular-hours bar open
- MSTR filtered to US/Eastern 09:30-16:00 (NYSE regular session)
- Binary position: fully in or fully out
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

MARKET_TZ = "US/Eastern"
MARKET_OPEN = "09:30"
MARKET_CLOSE = "16:00"


@dataclass
class StrategyConfig:
    display_name: str = "EchoTrend 240"
    internal_code: str = "ET240"
    ma_window: int = 240
    bear_confirm_bars: int = 3
    bull_confirm_bars: int = 3
    bear_ceiling: float = 0.0
    bull_ceiling: float = 1.10
    hysteresis_pct: float = 0.0
    freeze_bars: int = 0
    commission_rate: float = 0.0002
    slippage_rate: float = 0.0003
    timeframe: str = "1H"
    exec_timeframe: str = "1H"
    regime_timeframe: str = "4H"
    symbol: str = "MSTR"
    regime_symbol: str = "BTC-USDT"
    data_source: str = "local"
    regular_hours_only: bool = True
    warmup_bars: int = 960


@dataclass
class Signal:
    action: str  # "buy", "sell", "hold"
    price: float
    ma_value: float
    timestamp: pd.Timestamp
    hold_bars: int = 0
    reason: str = ""
    regime: str = ""


def _filter_regular_hours(df: pd.DataFrame) -> pd.DataFrame:
    """Filter MSTR data to NYSE regular session (09:30-16:00 US/Eastern)."""
    tmp = df.copy()
    ts = tmp["timestamp"]
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")
    et = ts.dt.tz_convert(MARKET_TZ)
    time_vals = et.dt.time
    start = pd.Timestamp(MARKET_OPEN).time()
    end = pd.Timestamp(MARKET_CLOSE).time()
    mask = (time_vals >= start) & (time_vals < end)
    return tmp[mask].reset_index(drop=True)


def _resample_to_4h(btc_1h: pd.DataFrame) -> pd.DataFrame:
    """Resample BTC 1H OHLCV to 4H bars."""
    df = btc_1h.copy()
    df = df.set_index("timestamp").sort_index()
    resampled = df.resample("4h", offset="0h").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    return resampled.reset_index()


def _compute_regime(btc_4h: pd.DataFrame, config: StrategyConfig) -> pd.DataFrame:
    """Compute regime signal from BTC 4H data with confirmation bars.

    Returns DataFrame with columns: [timestamp, close_4h, sma240, raw_above,
    regime] where regime is 1.0 (bull) or 0.0 (bear).
    Signal is shifted by 1 bar to avoid lookahead.
    """
    df = btc_4h.copy().sort_values("timestamp").reset_index(drop=True)
    df["sma240"] = df["close"].rolling(
        window=config.ma_window, min_periods=config.ma_window
    ).mean()
    df["raw_above"] = (df["close"] > df["sma240"]).astype(float)

    regime = np.full(len(df), np.nan)
    current_regime = 0.0
    counter = 0

    for i in range(config.ma_window, len(df)):
        above = df.iloc[i]["raw_above"]
        if pd.isna(above):
            regime[i] = current_regime
            continue

        if current_regime == 0.0:
            if above == 1.0:
                counter += 1
                if counter >= config.bull_confirm_bars:
                    current_regime = 1.0
                    counter = 0
            else:
                counter = 0
        else:
            if above == 0.0:
                counter += 1
                if counter >= config.bear_confirm_bars:
                    current_regime = 0.0
                    counter = 0
            else:
                counter = 0

        regime[i] = current_regime

    df["regime"] = regime
    df["regime"] = df["regime"].shift(1)

    return df[["timestamp", "close", "sma240", "raw_above", "regime"]].rename(
        columns={"close": "close_4h"}
    )


def _map_regime_to_1h(
    regime_4h: pd.DataFrame, mstr_1h: pd.DataFrame
) -> pd.DataFrame:
    """Forward-fill 4H regime signal onto MSTR 1H timestamps."""
    regime = regime_4h[["timestamp", "regime", "sma240"]].copy()
    regime = regime.rename(columns={"timestamp": "regime_ts"})
    regime = regime.sort_values("regime_ts")

    mstr = mstr_1h.copy().sort_values("timestamp")
    mstr["regime"] = np.nan
    mstr["sma240"] = np.nan

    regime_idx = 0
    for i in range(len(mstr)):
        ts = mstr.iloc[i]["timestamp"]
        while (
            regime_idx < len(regime) - 1
            and regime.iloc[regime_idx + 1]["regime_ts"] <= ts
        ):
            regime_idx += 1
        if regime.iloc[regime_idx]["regime_ts"] <= ts:
            mstr.iloc[i, mstr.columns.get_loc("regime")] = regime.iloc[regime_idx]["regime"]
            mstr.iloc[i, mstr.columns.get_loc("sma240")] = regime.iloc[regime_idx]["sma240"]

    return mstr


def _prepare_mstr(
    df: pd.DataFrame, btc_df: pd.DataFrame, config: StrategyConfig,
) -> pd.DataFrame:
    """Shared pipeline: filter MSTR to regular hours, map regime from BTC 4H."""
    btc_4h = _resample_to_4h(btc_df)
    regime_4h = _compute_regime(btc_4h, config)
    mstr_rth = _filter_regular_hours(df) if config.regular_hours_only else df.copy()
    return _map_regime_to_1h(regime_4h, mstr_rth)


def compute_signals(
    df: pd.DataFrame,
    config: StrategyConfig | None = None,
    *,
    btc_df: pd.DataFrame | None = None,
) -> list[Signal]:
    """Compute trading signals from MSTR 1H + BTC 1H data.

    MSTR data is filtered to NYSE regular session (09:30-16:00 ET).
    Regime flip detected on bar i queues a pending order; execution
    happens on bar i+1's open price (true next-bar execution).
    """
    if config is None:
        config = StrategyConfig()

    if btc_df is None:
        raise ValueError("btc_df is required for EchoTrend 240 (dual-feed strategy)")

    mstr = _prepare_mstr(df, btc_df, config)

    signals: list[Signal] = []
    in_position = False
    entry_bar_idx = 0
    pending_action: str | None = None

    for i in range(1, len(mstr)):
        row = mstr.iloc[i]
        prev = mstr.iloc[i - 1]
        sma = row["sma240"]

        # Execute pending order from previous bar at current bar's open
        if pending_action == "buy" and not in_position:
            signals.append(Signal(
                action="buy",
                price=row["open"],
                ma_value=sma if not pd.isna(sma) else 0.0,
                timestamp=row["timestamp"],
                reason="Regime flipped to bull — execute at next open",
                regime="bull",
            ))
            in_position = True
            entry_bar_idx = i
            pending_action = None
        elif pending_action == "sell" and in_position:
            hold_bars = i - entry_bar_idx
            signals.append(Signal(
                action="sell",
                price=row["open"],
                ma_value=sma if not pd.isna(sma) else 0.0,
                timestamp=row["timestamp"],
                hold_bars=hold_bars,
                reason="Regime flipped to bear — execute at next open",
                regime="bear",
            ))
            in_position = False
            pending_action = None

        # Detect regime flip on current bar → queue for next bar
        regime = row["regime"]
        prev_regime = prev["regime"]

        if pd.isna(regime) or pd.isna(prev_regime):
            continue

        if not in_position and prev_regime == 0.0 and regime == 1.0:
            pending_action = "buy"
        elif in_position and prev_regime == 1.0 and regime == 0.0:
            pending_action = "sell"

    return signals


def get_current_signal(
    df: pd.DataFrame,
    in_position: bool,
    entry_bar_idx: int = 0,
    config: StrategyConfig | None = None,
    *,
    btc_df: pd.DataFrame | None = None,
) -> Signal:
    """Get the signal for the latest bar.

    Uses the filtered MSTR index space for hold_bars computation.
    """
    if config is None:
        config = StrategyConfig()

    if btc_df is None:
        raise ValueError("btc_df is required for EchoTrend 240 (dual-feed strategy)")

    mstr = _prepare_mstr(df, btc_df, config)

    if len(mstr) < 3:
        return Signal(
            action="hold", price=0.0, ma_value=0.0,
            timestamp=pd.Timestamp.now(tz="UTC"),
            reason="Insufficient data",
        )

    last = mstr.iloc[-1]
    prev = mstr.iloc[-2]
    prev2 = mstr.iloc[-3]
    price = last["close"]
    sma = last["sma240"]
    regime = last["regime"]
    prev_regime = prev["regime"]
    prev2_regime = prev2["regime"]

    if pd.isna(regime):
        return Signal(
            action="hold",
            price=price,
            ma_value=0.0,
            timestamp=last["timestamp"],
            reason="Insufficient data for regime computation",
        )

    regime_str = "bull" if regime == 1.0 else "bear"

    # Check if there's a pending order from prev bar that should execute now
    if not in_position:
        if not pd.isna(prev2_regime) and prev2_regime == 0.0 and prev_regime == 1.0:
            # Regime flipped on prev bar → execute buy at last bar's open
            return Signal(
                action="buy",
                price=last["open"],
                ma_value=sma if not pd.isna(sma) else 0.0,
                timestamp=last["timestamp"],
                reason="Regime flipped to bull — execute at next open",
                regime="bull",
            )
        return Signal(
            action="hold",
            price=price,
            ma_value=sma if not pd.isna(sma) else 0.0,
            timestamp=last["timestamp"],
            reason=f"No position, regime is {regime_str}",
            regime=regime_str,
        )

    # Recompute hold_bars from signals in filtered space
    all_signals = compute_signals(df, config, btc_df=btc_df)
    last_buy = None
    for s in all_signals:
        if s.action == "buy":
            last_buy = s
    hold_bars = 0
    if last_buy is not None:
        buy_mask = mstr["timestamp"] == last_buy.timestamp
        if buy_mask.any():
            buy_idx = int(buy_mask.idxmax())
            hold_bars = len(mstr) - 1 - buy_idx

    # Check if pending sell from prev bar
    if not pd.isna(prev2_regime) and prev2_regime == 1.0 and prev_regime == 0.0:
        return Signal(
            action="sell",
            price=last["open"],
            ma_value=sma if not pd.isna(sma) else 0.0,
            timestamp=last["timestamp"],
            hold_bars=hold_bars,
            reason="Regime flipped to bear — execute at next open",
            regime="bear",
        )

    return Signal(
        action="hold",
        price=price,
        ma_value=sma if not pd.isna(sma) else 0.0,
        timestamp=last["timestamp"],
        hold_bars=hold_bars,
        reason=f"Holding, regime is {regime_str}",
        regime=regime_str,
    )


def get_filtered_df(
    df: pd.DataFrame,
    config: StrategyConfig | None = None,
) -> pd.DataFrame:
    """Return MSTR data filtered to regular hours for use by the runner."""
    if config is None:
        config = StrategyConfig()
    if config.regular_hours_only:
        return _filter_regular_hours(df)
    return df.copy()
