from __future__ import annotations

"""DualMom-B FastRe - Signal Logic (QQQ/SPY/CASH Momentum Rotation)

Asymmetric frequency dual-asset rotation:
- Monthly exit: QQQ <= SMA225 → CASH
- Weekly reentry: QQQ > SMA225 AND SPY 12m > 0 → re-enter
- Asset selection: QQQ 12m > SPY 12m → QQQ, else → SPY

Source: ndx100etf-a-strategy-clowder/src/backtest_qqq_relstrength.py
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from pipeline.backtest import BacktestResult, Trade


@dataclass
class StrategyConfig:
    display_name: str = "DualMom-B FastRe"
    internal_code: str = "dualmom_b_fastre"
    sma_window: int = 225
    momentum_lookback_months: int = 12
    reentry_freq: str = "W"
    require_abs_momentum: bool = True
    fee_rate: float = 0.001
    timeframe: str = "1D"
    symbol: str = "QQQ"
    secondary_symbol: str = "SPY"


@dataclass
class Signal:
    action: str
    price: float
    ma_value: float
    timestamp: pd.Timestamp
    holding: str = "CASH"
    qqq_12m: str = ""
    spy_12m: str = ""
    reason: str = ""


def _to_daily_index(df: pd.DataFrame) -> pd.Series:
    """Extract close prices with normalized daily DatetimeIndex."""
    ts = pd.to_datetime(df["timestamp"])
    close = df["close"].values
    idx = ts.dt.tz_localize(None).dt.normalize()
    s = pd.Series(close, index=idx, name="close")
    s = s[~s.index.duplicated(keep="last")]
    return s


def _generate_signal(
    qqq_close: pd.Series,
    spy_close: pd.Series,
    config: StrategyConfig,
) -> pd.Series:
    """Generate DualMom-B FastRe signal series. 1.0=QQQ, 0.5=SPY, 0.0=CASH."""
    sma = qqq_close.rolling(config.sma_window).mean()
    lookback = config.momentum_lookback_months

    spy_monthly = spy_close.resample("ME").last()
    qqq_monthly = qqq_close.resample("ME").last()
    spy_12m = spy_monthly.pct_change(lookback)
    qqq_12m = qqq_monthly.pct_change(lookback)

    sma_monthly = sma.resample("ME").last()
    qqq_close_monthly = qqq_close.resample("ME").last()

    monthly_signal = pd.Series(0.0, index=spy_monthly.index)
    risk_on = qqq_close_monthly > sma_monthly
    qqq_stronger = qqq_12m > spy_12m
    monthly_signal[risk_on & qqq_stronger] = 1.0
    monthly_signal[risk_on & ~qqq_stronger] = 0.5

    baseline_daily = monthly_signal.reindex(qqq_close.index, method="ffill").fillna(0)

    _weekly_raw = qqq_close.resample(config.reentry_freq).apply(
        lambda x: x.index[-1] if len(x) > 0 else None
    ).dropna()
    weekly_last_dates = set(pd.DatetimeIndex(_weekly_raw.values))

    signal = baseline_daily.copy()
    reentry_active = False
    reentry_val = 0.0

    for i, date in enumerate(qqq_close.index):
        base_val = baseline_daily.iloc[i]

        if base_val > 0:
            reentry_active = False
            signal.iloc[i] = base_val
            continue

        if reentry_active:
            signal.iloc[i] = reentry_val
            continue

        if date not in weekly_last_dates:
            signal.iloc[i] = 0.0
            continue

        sma_val = sma.iloc[i]
        if pd.isna(sma_val) or qqq_close.iloc[i] <= sma_val:
            signal.iloc[i] = 0.0
            continue

        if config.require_abs_momentum:
            applicable_spy_12m = spy_12m.loc[:date].dropna()
            if len(applicable_spy_12m) == 0 or applicable_spy_12m.iloc[-1] <= 0:
                signal.iloc[i] = 0.0
                continue

        applicable_qqq_12m = qqq_12m.loc[:date].dropna()
        applicable_spy_12m = spy_12m.loc[:date].dropna()
        if len(applicable_qqq_12m) > 0 and len(applicable_spy_12m) > 0:
            reentry_val = 1.0 if applicable_qqq_12m.iloc[-1] > applicable_spy_12m.iloc[-1] else 0.5
        else:
            reentry_val = 1.0

        reentry_active = True
        signal.iloc[i] = reentry_val

    return signal


def compute_signals(
    df: pd.DataFrame,
    config: StrategyConfig | None = None,
    *,
    extra_data: dict | None = None,
) -> list[Signal]:
    """Compute trading signals for the full history."""
    if config is None:
        config = StrategyConfig()
    extra_data = extra_data or {}

    qqq_close = _to_daily_index(df)
    spy_df = extra_data.get("spy")
    if spy_df is None:
        raise ValueError("spy data required in extra_data")
    spy_close = _to_daily_index(spy_df)

    common = qqq_close.index.intersection(spy_close.index)
    qqq_close = qqq_close.loc[common]
    spy_close = spy_close.loc[common]

    sig = _generate_signal(qqq_close, spy_close, config)
    sma = qqq_close.rolling(config.sma_window).mean()

    valid_start = common[config.sma_window]
    sig = sig.loc[valid_start:]

    signals: list[Signal] = []
    prev_val = 0.0
    for date, val in sig.items():
        if val != prev_val:
            holding = "QQQ" if val == 1.0 else ("SPY" if val == 0.5 else "CASH")
            signals.append(Signal(
                action="rebalance",
                price=qqq_close.loc[date],
                ma_value=sma.loc[date] if not pd.isna(sma.loc[date]) else 0.0,
                timestamp=pd.Timestamp(date),
                holding=holding,
                reason=f"signal={val}",
            ))
            prev_val = val

    return signals


def get_current_signal(
    df: pd.DataFrame,
    in_position: bool,
    entry_bar_idx: int = 0,
    config: StrategyConfig | None = None,
    *,
    extra_data: dict | None = None,
) -> Signal:
    """Get the current signal state."""
    if config is None:
        config = StrategyConfig()
    extra_data = extra_data or {}

    qqq_close = _to_daily_index(df)
    spy_df = extra_data.get("spy")
    if spy_df is None:
        return Signal(
            action="hold", price=0.0, ma_value=0.0,
            timestamp=pd.Timestamp.now(tz="UTC"),
            reason="Missing SPY data",
        )
    spy_close = _to_daily_index(spy_df)

    common = qqq_close.index.intersection(spy_close.index)
    qqq_close = qqq_close.loc[common]
    spy_close = spy_close.loc[common]

    if len(qqq_close) < config.sma_window + 1:
        return Signal(
            action="hold", price=0.0, ma_value=0.0,
            timestamp=pd.Timestamp.now(tz="UTC"),
            reason="Insufficient data",
        )

    sig = _generate_signal(qqq_close, spy_close, config)
    sma = qqq_close.rolling(config.sma_window).mean()

    last_date = qqq_close.index[-1]
    last_sig = sig.iloc[-1]
    holding = "QQQ" if last_sig == 1.0 else ("SPY" if last_sig == 0.5 else "CASH")

    spy_monthly = spy_close.resample("ME").last()
    qqq_monthly = qqq_close.resample("ME").last()
    spy_12m = spy_monthly.pct_change(config.momentum_lookback_months).dropna()
    qqq_12m = qqq_monthly.pct_change(config.momentum_lookback_months).dropna()

    qqq_12m_str = f"+{qqq_12m.iloc[-1]:.2%}" if len(qqq_12m) > 0 else "N/A"
    spy_12m_str = f"+{spy_12m.iloc[-1]:.2%}" if len(spy_12m) > 0 else "N/A"

    return Signal(
        action="hold",
        price=qqq_close.iloc[-1],
        ma_value=sma.iloc[-1],
        timestamp=pd.Timestamp(last_date),
        holding=holding,
        qqq_12m=qqq_12m_str,
        spy_12m=spy_12m_str,
        reason=f"QQQ {'>' if last_sig > 0 else '<='} SMA{config.sma_window}, holding {holding}",
    )


def run_backtest(
    df: pd.DataFrame,
    config: StrategyConfig | None = None,
    *,
    extra_data: dict | None = None,
    initial_capital: float = 10000.0,
    fee_rate: float = 0.001,
) -> BacktestResult:
    """Run path-level fee-adjusted backtest with Next Open execution."""
    if config is None:
        config = StrategyConfig()
    extra_data = extra_data or {}

    qqq_close = _to_daily_index(df)
    spy_df = extra_data.get("spy")
    if spy_df is None:
        raise ValueError("spy data required in extra_data for backtest")
    spy_close = _to_daily_index(spy_df)

    common = qqq_close.index.intersection(spy_close.index)
    qqq_close = qqq_close.loc[common]
    spy_close = spy_close.loc[common]

    # Build OHLC frames for Next Open execution
    qqq_df_aligned = pd.DataFrame({
        "open": pd.Series(df.set_index(pd.to_datetime(df["timestamp"]).dt.tz_localize(None).dt.normalize())["open"]).reindex(common),
        "close": qqq_close,
    })
    spy_df_aligned = pd.DataFrame({
        "open": pd.Series(spy_df.set_index(pd.to_datetime(spy_df["timestamp"]).dt.tz_localize(None).dt.normalize())["open"]).reindex(common),
        "close": spy_close,
    })

    sig = _generate_signal(qqq_close, spy_close, config)
    valid_start = common[config.sma_window]
    sig = sig.loc[valid_start:]

    position = sig.shift(1).fillna(0)
    prev_position = position.shift(1).fillna(0)
    is_trade_day = (position != prev_position)

    qqq_ret = qqq_close.pct_change().fillna(0).loc[valid_start:]
    spy_ret = spy_close.pct_change().fillna(0).loc[valid_start:]
    qqq_ovn = (qqq_df_aligned["open"] / qqq_df_aligned["close"].shift(1) - 1).fillna(0).loc[valid_start:]
    qqq_intra = (qqq_df_aligned["close"] / qqq_df_aligned["open"] - 1).fillna(0).loc[valid_start:]
    spy_ovn = (spy_df_aligned["open"] / spy_df_aligned["close"].shift(1) - 1).fillna(0).loc[valid_start:]
    spy_intra = (spy_df_aligned["close"] / spy_df_aligned["open"] - 1).fillna(0).loc[valid_start:]

    strat_ret = pd.Series(0.0, index=sig.index)
    for i in range(len(strat_ret)):
        pos = position.iloc[i]
        prev_pos = prev_position.iloc[i]
        if is_trade_day.iloc[i]:
            ovn = qqq_ovn.iloc[i] if prev_pos == 1.0 else (spy_ovn.iloc[i] if prev_pos == 0.5 else 0.0)
            intra = qqq_intra.iloc[i] if pos == 1.0 else (spy_intra.iloc[i] if pos == 0.5 else 0.0)
            fee_cost = (fee_rate if prev_pos > 0 else 0) + (fee_rate if pos > 0 else 0)
            strat_ret.iloc[i] = ovn + intra - fee_cost
        else:
            if pos == 1.0:
                strat_ret.iloc[i] = qqq_ret.iloc[i]
            elif pos == 0.5:
                strat_ret.iloc[i] = spy_ret.iloc[i]

    nav = (1 + strat_ret).cumprod() * initial_capital
    total_return_pct = (nav.iloc[-1] / initial_capital - 1) * 100
    max_dd_pct = ((nav / nav.cummax() - 1).min()) * 100
    years = len(nav) / 252
    cagr = (nav.iloc[-1] / initial_capital) ** (1 / years) - 1 if years > 0 else 0
    sharpe = strat_ret.mean() / strat_ret.std() * np.sqrt(252) if strat_ret.std() > 0 else 0
    num_trades = int(is_trade_day.sum())

    equity_curve = pd.DataFrame({
        "timestamp": sig.index,
        "equity": nav.values,
    })

    # B&H QQQ for comparison
    bh_nav = (1 + qqq_ret).cumprod()
    bh_return = (bh_nav.iloc[-1] - 1) * 100
    bh_dd = ((bh_nav / bh_nav.cummax() - 1).min()) * 100

    return BacktestResult(
        config=config,
        trades=[],
        equity_curve=equity_curve,
        total_return_pct=round(total_return_pct, 2),
        realized_return_pct=round(total_return_pct, 2),
        max_drawdown_pct=round(abs(max_dd_pct), 2),
        win_rate=0.0,
        total_trades=num_trades,
        avg_hold_bars=round(len(nav) / max(num_trades, 1), 1),
        sharpe_ratio=round(sharpe, 2),
        start_date=str(sig.index[0].date()),
        end_date=str(sig.index[-1].date()),
        buy_hold_return_pct=round(bh_return, 2),
        buy_hold_max_drawdown_pct=round(abs(bh_dd), 2),
        has_open_position=(position.iloc[-1] > 0),
    )
