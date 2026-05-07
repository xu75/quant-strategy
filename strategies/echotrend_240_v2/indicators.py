from __future__ import annotations

"""Feature computation for EchoTrend 240.

Ported from mstr-strategy-clowder/src/indicators.py.
Computes intraday features, daily trend features, and BTC 4H regime signal.
"""

import pandas as pd
import numpy as np


MARKET_TZ = "US/Eastern"
MARKET_OPEN = "09:30"
MARKET_CLOSE = "16:00"


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    return (100 - (100 / (1 + rs))).fillna(50)


def filter_regular_hours(df: pd.DataFrame) -> pd.DataFrame:
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


def align_to_mstr_session(
    mstr: pd.DataFrame,
    btc: pd.DataFrame,
    market: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame | None]:
    """Align BTC and market data to MSTR regular-session index via ffill."""
    btc_aligned = btc.set_index("timestamp")["close"].reindex(
        mstr.set_index("timestamp").index, method="ffill"
    )
    btc_out = btc.set_index("timestamp").reindex(
        mstr.set_index("timestamp").index, method="ffill"
    ).reset_index()
    btc_out = btc_out.rename(columns={"index": "timestamp"}) if "index" in btc_out.columns else btc_out

    market_out = None
    if market is not None and not market.empty:
        market_out = market.set_index("timestamp").reindex(
            mstr.set_index("timestamp").index, method="ffill"
        ).reset_index()
        market_out = market_out.rename(columns={"index": "timestamp"}) if "index" in market_out.columns else market_out

    return mstr, btc_out, market_out


def add_features(
    mstr: pd.DataFrame,
    btc: pd.DataFrame,
    market: pd.DataFrame | None = None,
    rsi_window: int = 14,
) -> pd.DataFrame:
    """Compute intraday features on MSTR-session-aligned data.

    Input DataFrames must share the same index (aligned via align_to_mstr_session).
    Source: mstr-strategy-clowder/src/indicators.py:add_features()
    """
    df = mstr.set_index("timestamp").sort_index().copy()
    btc_s = btc.set_index("timestamp").sort_index()
    btc_close = btc_s["close"].reindex(df.index, method="ffill")

    session = df.index.date
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_dollar_volume = (typical * df["volume"]).groupby(session).cumsum()
    cum_volume = df["volume"].groupby(session).cumsum()
    df["vwap"] = cum_dollar_volume / cum_volume.replace(0, pd.NA)
    df["rsi"] = rsi(df["close"], rsi_window)
    df["mstr_1h_return"] = df["close"].pct_change(1)
    df["mstr_4h_return"] = df["close"].pct_change(4)
    df["btc_close"] = btc_close
    df["btc_1h_return"] = btc_close.pct_change(1)
    df["btc_4h_return"] = btc_close.pct_change(4)

    if market is not None and not market.empty:
        mkt_s = market.set_index("timestamp").sort_index()
        market_close = mkt_s["close"].reindex(df.index, method="ffill")
        df["market_close"] = market_close
        df["market_1h_return"] = market_close.pct_change(1)
        df["market_4h_return"] = market_close.pct_change(4)
    else:
        df["market_close"] = pd.NA
        df["market_1h_return"] = 0.0
        df["market_4h_return"] = 0.0

    df["vwap_deviation"] = df["close"] / df["vwap"] - 1
    df["mstr_btc_rs_1h"] = df["mstr_1h_return"] - df["btc_1h_return"]
    df["mstr_btc_rs_4h"] = df["mstr_4h_return"] - df["btc_4h_return"]
    rs_ratio = df["close"] / btc_close.replace(0, pd.NA)
    df["mstr_btc_ratio_20"] = rs_ratio / rs_ratio.rolling(20, min_periods=5).mean() - 1

    daily_high = df["high"].groupby(session).cummax()
    daily_low = df["low"].groupby(session).cummin()
    df["day_low"] = daily_low
    daily_close = df["close"].groupby(session).last()
    prev_close_by_day = daily_close.shift(1)
    prev_close = pd.Series(session, index=df.index).map(prev_close_by_day)
    df["prev_close"] = prev_close.fillna(df["close"].iloc[0])
    df["day_range"] = (daily_high - daily_low) / df["prev_close"]

    return df.dropna(subset=["vwap", "btc_close"])


def _daily_trend_frame(daily: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """Compute daily trend features for one ticker."""
    close = daily["close"].copy()
    out = pd.DataFrame(index=daily.index)
    out[f"{prefix}_daily_close"] = close
    out[f"{prefix}_daily_5d_return"] = close.pct_change(5)
    out[f"{prefix}_daily_10d_return"] = close.pct_change(10)
    out[f"{prefix}_daily_20d_return"] = close.pct_change(20)
    out[f"{prefix}_daily_ema20"] = close.ewm(span=20, min_periods=20, adjust=False).mean()
    out[f"{prefix}_daily_ema50"] = close.ewm(span=50, min_periods=50, adjust=False).mean()
    out[f"{prefix}_daily_above_ema20"] = (close > out[f"{prefix}_daily_ema20"]).astype(float)
    out[f"{prefix}_daily_above_ema50"] = (close > out[f"{prefix}_daily_ema50"]).astype(float)
    daily_ret = close.pct_change()
    out[f"{prefix}_daily_vol20"] = daily_ret.rolling(20, min_periods=10).std()
    out[f"{prefix}_daily_vol10"] = daily_ret.rolling(10, min_periods=5).std()
    return out


def add_daily_trend_features(
    features: pd.DataFrame,
    mstr_daily: pd.DataFrame,
    btc_daily: pd.DataFrame,
    market_daily: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Add daily trend features with shift(1) lookahead protection.

    Source: mstr-strategy-clowder/src/indicators.py:add_daily_trend_features()
    UTC session_date + shift(1) ensures daily features are only available
    after the daily bar closes.
    """
    df = features.copy()
    daily_parts = [
        _daily_trend_frame(mstr_daily.set_index("timestamp").sort_index(), "mstr"),
        _daily_trend_frame(btc_daily.set_index("timestamp").sort_index(), "btc"),
    ]
    if market_daily is not None and not market_daily.empty:
        daily_parts.append(_daily_trend_frame(market_daily.set_index("timestamp").sort_index(), "market"))
    daily = pd.concat(daily_parts, axis=1, sort=True).sort_index()
    daily = daily.ffill()

    daily["session_date"] = daily.index.tz_convert("UTC").strftime("%Y-%m-%d")
    daily = daily.drop_duplicates("session_date", keep="last").set_index("session_date")
    daily = daily.shift(1)

    session_dates = pd.Series(df.index.tz_convert("UTC").strftime("%Y-%m-%d"), index=df.index)
    for col in daily.columns:
        df[col] = session_dates.map(daily[col])

    defaults = {
        "mstr_daily_5d_return": 0.0, "mstr_daily_10d_return": 0.0, "mstr_daily_20d_return": 0.0,
        "btc_daily_5d_return": 0.0, "btc_daily_10d_return": 0.0, "btc_daily_20d_return": 0.0,
        "market_daily_5d_return": 0.0, "market_daily_10d_return": 0.0, "market_daily_20d_return": 0.0,
        "mstr_daily_above_ema20": 0.0, "mstr_daily_above_ema50": 0.0,
        "btc_daily_above_ema20": 0.0, "btc_daily_above_ema50": 0.0,
        "market_daily_above_ema20": 0.0, "market_daily_above_ema50": 0.0,
        "mstr_daily_vol20": 0.0, "mstr_daily_vol10": 0.0,
        "btc_daily_vol20": 0.0, "btc_daily_vol10": 0.0,
    }
    for col, default in defaults.items():
        if col not in df.columns:
            df[col] = default
        else:
            df[col] = df[col].fillna(default)
    return df


def add_btc_4h_trend_features(
    features: pd.DataFrame,
    btc_raw: pd.DataFrame,
    ma_period: int = 240,
) -> pd.DataFrame:
    """Add BTC 4H SMA regime signal with shift(1) lookahead protection.

    Source: mstr-strategy-clowder/src/indicators.py:add_btc_4h_trend_features()
    """
    df = features.copy()
    btc_idx = btc_raw.set_index("timestamp").sort_index()
    btc_4h = (
        btc_idx.resample("4h")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["open", "close"])
    )
    close_4h = btc_4h["close"]
    sma = close_4h.rolling(ma_period, min_periods=ma_period).mean()
    above = (close_4h > sma).astype(float)
    above_shifted = above.shift(1)
    col_name = f"btc_4h_above_sma{ma_period}"
    df[col_name] = above_shifted.reindex(df.index, method="ffill").fillna(0.0)
    return df
