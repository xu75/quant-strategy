"""
N100 Guard-Z: 纳指100ETF防守轮动策略
DualMom-B FastRe 择时 + Hybrid Z-Score 溢价轮动
"""

from dataclasses import dataclass, field
from typing import Optional
import numpy as np
import pandas as pd

from pipeline.backtest import BacktestResult, Trade


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ETF_NAMES = {
    "513100": "国泰纳指100",
    "159941": "易方达纳指100",
    "513300": "华夏纳指100",
    "159501": "富国纳指100",
    "159513": "鹏华纳指100",
    "159659": "景顺纳指100",
    "159632": "中欧纳指100",
    "159660": "博时纳指100",
    "159696": "华安纳指100",
    "513110": "华安纳指100ETF",
    "513390": "天弘纳指100",
}


@dataclass
class StrategyConfig:
    sma_window: int = 225
    momentum_lookback_months: int = 12
    reentry_freq: str = "W"
    require_abs_momentum: bool = True
    zscore_lookback: int = 60
    ipo_warmup: int = 60
    switch_zscore_threshold: float = 1.0
    switch_premium_threshold: float = 0.0016
    switch_holding_days: int = 10
    fee_rate: float = 0.001
    money_market_annual_rate: float = 0.02
    timeframe: str = "1D"
    symbol: str = "QQQ"
    secondary_symbol: str = "SPY"
    display_name: str = "N100 Guard-Z"
    internal_code: str = "n100_guard_z"


# ---------------------------------------------------------------------------
# Signal dataclass
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    action: str  # "risk_on" / "risk_off"
    price: float = 0.0
    timestamp: Optional[pd.Timestamp] = None
    reason: str = ""
    ma_value: float = 0.0
    hold_bars: int = 0
    qqq_12m: float = 0.0
    spy_12m: float = 0.0
    state: str = ""  # NDX_INVESTED / TRUE_CASH_STRETCH
    current_etf: str = ""
    holding: str = ""
    current_zscore: float = 0.0
    current_premium: float = 0.0


# ---------------------------------------------------------------------------
# Signal Layer: DualMom-B FastRe (binary: NDX / CASH)
# ---------------------------------------------------------------------------

def _compute_momentum(series: pd.Series, months: int = 12) -> pd.Series:
    bars = months * 21
    return series / series.shift(bars) - 1


def _is_month_end(dates: pd.DatetimeIndex) -> pd.Series:
    return pd.Series(dates, index=dates).dt.month != pd.Series(dates, index=dates).shift(-1).dt.month


def _is_week_end(dates: pd.DatetimeIndex) -> pd.Series:
    return pd.Series(dates, index=dates).dt.weekday >= pd.Series(dates, index=dates).shift(-1).dt.weekday


def _fastre_signals(qqq_df: pd.DataFrame, spy_df: pd.DataFrame,
                    config: StrategyConfig) -> pd.DataFrame:
    """Generate FastRe signal layer: binary NDX/CASH states."""
    merged = qqq_df[["timestamp", "close"]].rename(columns={"close": "qqq_close"})
    spy_tmp = spy_df[["timestamp", "close"]].rename(columns={"close": "spy_close"})
    merged = merged.merge(spy_tmp, on="timestamp", how="inner").sort_values("timestamp").reset_index(drop=True)

    merged["sma"] = merged["qqq_close"].rolling(config.sma_window).mean()
    merged["qqq_12m"] = _compute_momentum(merged["qqq_close"], config.momentum_lookback_months)
    merged["spy_12m"] = _compute_momentum(merged["spy_close"], config.momentum_lookback_months)

    dates = pd.DatetimeIndex(merged["timestamp"])
    is_month_end = _is_month_end(dates).values
    is_week_end = _is_week_end(dates).values

    n = len(merged)
    states = [""] * n
    state = "NDX_INVESTED"

    for i in range(config.sma_window, n):
        qqq_close = merged.iloc[i]["qqq_close"]
        sma_val = merged.iloc[i]["sma"]
        qqq_mom = merged.iloc[i]["qqq_12m"]
        spy_mom = merged.iloc[i]["spy_12m"]

        if state == "NDX_INVESTED":
            if is_month_end[i] and qqq_close <= sma_val:
                state = "TRUE_CASH_STRETCH"
            elif is_month_end[i] and qqq_close > sma_val:
                if spy_mom > qqq_mom:
                    state = "SPY_INVESTED"
                # else stay NDX_INVESTED
        elif state == "SPY_INVESTED":
            # Binary mapping: SPY_INVESTED → CASH (no re-entry trigger)
            if is_month_end[i]:
                if qqq_close <= sma_val:
                    state = "TRUE_CASH_STRETCH"
                elif qqq_mom >= spy_mom:
                    state = "NDX_INVESTED"
        elif state == "TRUE_CASH_STRETCH":
            if is_week_end[i]:
                if (qqq_close > sma_val
                        and spy_mom > 0
                        and qqq_mom > spy_mom):
                    state = "NDX_INVESTED"

        states[i] = state

    merged["state"] = states
    # Binary mapping: NDX_INVESTED → risk_on, everything else → risk_off
    merged["risk_on"] = merged["state"] == "NDX_INVESTED"
    return merged


# ---------------------------------------------------------------------------
# Execution Layer: Hybrid Z-Score Premium Rotation
# ---------------------------------------------------------------------------

def _compute_tradable_premium(etf_close: pd.Series, nav: pd.Series,
                              qqq_return_1d: pd.Series) -> pd.Series:
    estimated_nav = nav.shift(1) * (1 + qqq_return_1d)
    return etf_close / estimated_nav - 1


def _zscore_rotation(etf_df: pd.DataFrame, nav_df: pd.DataFrame,
                     qqq_returns: pd.Series, config: StrategyConfig,
                     risk_on_mask: pd.Series) -> pd.DataFrame:
    """
    Hybrid Z-Score rotation among ETF pool.
    Returns DataFrame with columns: date, selected_etf, zscore, premium
    """
    etf_codes = [c for c in etf_df.columns if c != "timestamp"]
    nav_codes = [c for c in nav_df.columns if c != "timestamp"]

    # Keep timezone-aware pandas timestamps; `.values` strips tz info and breaks
    # timestamp-keyed joins used by current-signal reporting.
    dates = etf_df["timestamp"].reset_index(drop=True)
    n = len(dates)

    selected_etfs = [""] * n
    selected_zscores = [0.0] * n
    selected_premiums = [0.0] * n

    # Compute premiums and z-scores for all ETFs
    premiums = {}
    zscores = {}
    for code in etf_codes:
        nav_col = code if code in nav_codes else None
        if nav_col is None:
            continue
        prem = _compute_tradable_premium(etf_df[code], nav_df[nav_col], qqq_returns)
        premiums[code] = prem
        z = (prem - prem.rolling(config.zscore_lookback).mean()) / prem.rolling(config.zscore_lookback).std()
        zscores[code] = z

    premium_df = pd.DataFrame(premiums)
    zscore_df = pd.DataFrame(zscores)

    current_etf = ""
    holding_days = 0

    for i in range(n):
        if not risk_on_mask.iloc[i]:
            current_etf = ""
            holding_days = 0
            selected_etfs[i] = ""
            continue

        holding_days += 1
        available = []
        for code in etf_codes:
            if code not in zscores:
                continue
            # IPO warmup check
            first_valid = etf_df[code].first_valid_index()
            if first_valid is None or i - first_valid < config.ipo_warmup:
                continue
            if pd.isna(zscore_df[code].iloc[i]):
                continue
            available.append(code)

        if not available:
            selected_etfs[i] = current_etf
            selected_zscores[i] = 0.0
            selected_premiums[i] = 0.0
            continue

        # First entry: pick lowest z-score
        if current_etf == "" or current_etf not in available:
            zs = {c: zscore_df[c].iloc[i] for c in available}
            best = min(zs, key=zs.get)
            current_etf = best
            holding_days = 1

        # Check switch condition
        elif holding_days >= config.switch_holding_days:
            curr_z = zscore_df[current_etf].iloc[i]
            curr_p = premium_df[current_etf].iloc[i]

            candidate_zs = {c: zscore_df[c].iloc[i] for c in available if c != current_etf}
            if candidate_zs:
                best_candidate = min(candidate_zs, key=candidate_zs.get)
                cand_z = candidate_zs[best_candidate]
                cand_p = premium_df[best_candidate].iloc[i]

                z_diff = curr_z - cand_z
                p_diff = curr_p - cand_p

                if (z_diff > config.switch_zscore_threshold
                        and p_diff > config.switch_premium_threshold):
                    current_etf = best_candidate
                    holding_days = 1

        selected_etfs[i] = current_etf
        selected_zscores[i] = zscore_df[current_etf].iloc[i] if current_etf in zscores else 0.0
        selected_premiums[i] = premium_df[current_etf].iloc[i] if current_etf in premiums else 0.0

    return pd.DataFrame({
        "timestamp": dates,
        "selected_etf": selected_etfs,
        "zscore": selected_zscores,
        "premium": selected_premiums,
    })


def _unpack_extra_data(kwargs: dict) -> tuple:
    """Extract spy_df, etf_df (close), etf_open_df, etf_adj_df, nav_df from extra_data.

    Runner passes extra_data keyed by manifest data_sources keys:
      spy, etf_513100, etf_159941, ..., etf_nav

    Returns:
      spy_df: SPY DataFrame (timestamp, close, ...)
      etf_df: wide DataFrame of ETF close prices (for premium/z-score)
      etf_open_df: wide DataFrame of ETF open prices (for T+1 execution)
      etf_adj_df: wide DataFrame of ETF adj_close (for split-adjusted returns)
      nav_df: wide NAV DataFrame
    """
    extra = kwargs.get("extra_data", kwargs)

    # SPY DataFrame
    spy_df = extra.get("spy")
    if spy_df is None:
        spy_df = kwargs.get("spy_df")

    # NAV DataFrame (wide format: timestamp + one column per ETF code)
    nav_df = extra.get("etf_nav")
    if nav_df is None:
        nav_df = kwargs.get("nav_df")

    # ETF daily data: build three wide DataFrames (close, open, adj_close)
    close_dfs = []
    open_dfs = []
    adj_dfs = []
    for key, val in extra.items():
        if key.startswith("etf_") and key != "etf_nav" and isinstance(val, pd.DataFrame):
            code = key.replace("etf_", "")
            # Close (for premium calculation)
            if "close" in val.columns:
                close_dfs.append(val[["timestamp", "close"]].rename(columns={"close": code}))
            # Open (for T+1 execution)
            if "open" in val.columns:
                open_dfs.append(val[["timestamp", "open"]].rename(columns={"open": code}))
            # Adj_close (for split-adjusted returns)
            if "adj_close" in val.columns:
                adj_dfs.append(val[["timestamp", "adj_close"]].rename(columns={"adj_close": code}))

    def _merge_wide(dfs):
        if not dfs:
            return None
        result = dfs[0]
        for tmp in dfs[1:]:
            result = result.merge(tmp, on="timestamp", how="outer")
        return result.sort_values("timestamp").reset_index(drop=True)

    etf_df = _merge_wide(close_dfs)
    if etf_df is None:
        etf_df = kwargs.get("etf_df")

    etf_open_df = _merge_wide(open_dfs)
    etf_adj_df = _merge_wide(adj_dfs)

    return spy_df, etf_df, etf_open_df, etf_adj_df, nav_df


# ---------------------------------------------------------------------------
# Public Interface
# ---------------------------------------------------------------------------

def compute_signals(df: pd.DataFrame, config: StrategyConfig = None,
                    **kwargs) -> list:
    """
    Compute full signal history.
    kwargs must include extra_data with: spy, etf_{code}s, etf_nav
    """
    if config is None:
        config = StrategyConfig()

    spy_df, etf_df, etf_open_df, etf_adj_df, nav_df = _unpack_extra_data(kwargs)

    if spy_df is None or etf_df is None or nav_df is None:
        raise ValueError("n100_guard_z requires spy, etf_{code}, etf_nav in extra_data")

    # Signal layer
    signal_layer = _fastre_signals(df, spy_df, config)

    # QQQ daily returns for premium estimation
    qqq_returns = signal_layer["qqq_close"].pct_change().fillna(0)

    # Normalize timestamps to date-only (midnight UTC) for alignment.
    # QQQ may carry 04:00Z while A-share ETFs use 00:00Z.
    signal_layer["timestamp"] = signal_layer["timestamp"].dt.normalize()
    etf_df["timestamp"] = etf_df["timestamp"].dt.normalize()
    nav_df["timestamp"] = nav_df["timestamp"].dt.normalize()

    # Align execution data by timestamp. ETF histories start later than QQQ/SPY,
    # so row-number alignment would drop the latest rotation state.
    common_dates = (
        set(signal_layer["timestamp"])
        & set(etf_df["timestamp"])
        & set(nav_df["timestamp"])
    )
    signal_aligned = (
        signal_layer[signal_layer["timestamp"].isin(common_dates)]
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    etf_aligned = (
        etf_df[etf_df["timestamp"].isin(common_dates)]
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    nav_aligned = (
        nav_df[nav_df["timestamp"].isin(common_dates)]
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    qqq_ret_aligned = signal_aligned["qqq_close"].pct_change().fillna(0)

    # Execution layer
    risk_on_mask = signal_aligned["risk_on"]
    rotation = _zscore_rotation(etf_aligned, nav_aligned, qqq_ret_aligned, config, risk_on_mask)
    rotation_by_ts = rotation.set_index("timestamp")

    # Build signal list
    signals = []
    for i in range(len(signal_layer)):
        row = signal_layer.iloc[i]
        row_ts = row["timestamp"]
        etf_info = rotation_by_ts.loc[row_ts] if row_ts in rotation_by_ts.index else None

        etf_code = etf_info["selected_etf"] if etf_info is not None else ""
        action = "risk_on" if row["risk_on"] else "risk_off"
        sig = Signal(
            action=action,
            price=row["qqq_close"],
            timestamp=pd.Timestamp(row["timestamp"]),
            reason=row["state"],
            ma_value=row["sma"] if not pd.isna(row["sma"]) else 0.0,
            qqq_12m=row["qqq_12m"] if not pd.isna(row["qqq_12m"]) else 0.0,
            spy_12m=row["spy_12m"] if not pd.isna(row["spy_12m"]) else 0.0,
            state=row["state"],
            current_etf=etf_code,
            holding=ETF_NAMES.get(etf_code, etf_code) if etf_code else "—",
            current_zscore=etf_info["zscore"] if etf_info is not None else 0.0,
            current_premium=etf_info["premium"] if etf_info is not None else 0.0,
        )
        signals.append(sig)

    return signals


def get_current_signal(df: pd.DataFrame, in_position: bool = False,
                       entry_bar_idx: int = 0, config: StrategyConfig = None,
                       **kwargs) -> Signal:
    """Return signal for the latest bar."""
    signals = compute_signals(df, config, **kwargs)
    if not signals:
        return Signal(action="risk_off", reason="no_data")
    return signals[-1]


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

def run_backtest(df: pd.DataFrame, config: StrategyConfig = None,
                 **kwargs) -> dict:
    """
    Run full backtest with:
    - Signal layer: FastRe binary (NDX/CASH)
    - Execution layer: Z-Score rotation among ETF pool
    - T+1 execution (signal on US close, execute on A-share next open)
    - Fee model: entry/exit = 1×fee_rate, rotation = 2×fee_rate
    """
    if config is None:
        config = StrategyConfig()

    spy_df, etf_df, etf_open_df, etf_adj_df, nav_df = _unpack_extra_data(kwargs)

    if spy_df is None or etf_df is None or nav_df is None:
        raise ValueError("n100_guard_z requires spy, etf_{code}, etf_nav in extra_data")

    # Signal layer
    signal_layer = _fastre_signals(df, spy_df, config)
    qqq_returns = signal_layer["qqq_close"].pct_change().fillna(0)

    # Normalize timestamps to date-only (midnight UTC) for alignment.
    signal_layer["timestamp"] = signal_layer["timestamp"].dt.normalize()
    etf_df["timestamp"] = etf_df["timestamp"].dt.normalize()
    nav_df["timestamp"] = nav_df["timestamp"].dt.normalize()
    if etf_open_df is not None:
        etf_open_df["timestamp"] = etf_open_df["timestamp"].dt.normalize()
    if etf_adj_df is not None:
        etf_adj_df["timestamp"] = etf_adj_df["timestamp"].dt.normalize()

    # Align data
    common_dates = set(signal_layer["timestamp"]) & set(etf_df["timestamp"]) & set(nav_df["timestamp"])
    signal_layer = signal_layer[signal_layer["timestamp"].isin(common_dates)].reset_index(drop=True)
    etf_aligned = etf_df[etf_df["timestamp"].isin(common_dates)].sort_values("timestamp").reset_index(drop=True)
    nav_aligned = nav_df[nav_df["timestamp"].isin(common_dates)].sort_values("timestamp").reset_index(drop=True)

    n = len(signal_layer)
    qqq_ret_aligned = signal_layer["qqq_close"].pct_change().fillna(0)

    # Rotation layer (uses close prices for z-score/premium)
    risk_on_mask = signal_layer["risk_on"]
    rotation = _zscore_rotation(etf_aligned, nav_aligned, qqq_ret_aligned, config, risk_on_mask)

    # Align open and adj_close DataFrames to same date range
    if etf_open_df is not None:
        open_aligned = etf_open_df[etf_open_df["timestamp"].isin(common_dates)].sort_values("timestamp").reset_index(drop=True)
    else:
        open_aligned = None
    if etf_adj_df is not None:
        adj_aligned = etf_adj_df[etf_adj_df["timestamp"].isin(common_dates)].sort_values("timestamp").reset_index(drop=True)
    else:
        adj_aligned = None

    # Backtest engine — T+1 adjusted-open execution
    # Signal on day i (US close) → execute at day i+1 A-share open
    # Execution price: adjusted_open = open × (adj_close / close)
    # Daily return for position held: adj_open[i+1] / adj_open[i] - 1
    # When open/adj data unavailable, fallback to close-to-close
    equity = [1.0]
    trades = []
    daily_mm_rate = config.money_market_annual_rate / 365

    prev_risk_on = False
    prev_etf = ""

    def _get_adj_open(bar_idx, etf_code):
        """Get adjusted open price for a given bar and ETF."""
        if (open_aligned is not None and adj_aligned is not None
                and etf_code in open_aligned.columns and etf_code in adj_aligned.columns
                and etf_code in etf_aligned.columns):
            o = open_aligned[etf_code].iloc[bar_idx]
            adj_c = adj_aligned[etf_code].iloc[bar_idx]
            c = etf_aligned[etf_code].iloc[bar_idx]
            if pd.notna(o) and pd.notna(adj_c) and pd.notna(c) and c > 0:
                return o * (adj_c / c)
        # Fallback: use close as proxy
        if etf_code in etf_aligned.columns:
            return etf_aligned[etf_code].iloc[bar_idx]
        return None

    for i in range(2, n):
        # Signal from previous day (T+1 lag)
        sig_idx = i - 1
        curr_risk_on = signal_layer["risk_on"].iloc[sig_idx]
        curr_etf = rotation["selected_etf"].iloc[sig_idx]
        daily_return = 0.0

        if curr_risk_on and curr_etf and curr_etf in etf_aligned.columns:
            # T+1 execution: use adjusted open prices
            exec_price_today = _get_adj_open(i, curr_etf)
            exec_price_yesterday = _get_adj_open(i - 1, curr_etf)

            if exec_price_today is not None and exec_price_yesterday is not None and exec_price_yesterday > 0:
                daily_return = exec_price_today / exec_price_yesterday - 1

            # Fee on transitions
            if not prev_risk_on:
                # Entry: 1×fee
                daily_return -= config.fee_rate
                trades.append({
                    "date": str(signal_layer["timestamp"].iloc[i]),
                    "type": "entry",
                    "etf": curr_etf,
                })
            elif prev_etf != curr_etf and prev_etf != "":
                # Rotation: 2×fee
                daily_return -= 2 * config.fee_rate
                trades.append({
                    "date": str(signal_layer["timestamp"].iloc[i]),
                    "type": "rotation",
                    "from": prev_etf,
                    "to": curr_etf,
                })
        else:
            # Risk-off: money market return
            daily_return = daily_mm_rate

            if prev_risk_on and prev_etf:
                # Exit: 1×fee
                daily_return -= config.fee_rate
                trades.append({
                    "date": str(signal_layer["timestamp"].iloc[i]),
                    "type": "exit",
                    "etf": prev_etf,
                })

        equity.append(equity[-1] * (1 + daily_return))
        prev_risk_on = curr_risk_on
        prev_etf = curr_etf if curr_risk_on else ""

    # Metrics
    equity_series = pd.Series(equity)
    total_return = equity_series.iloc[-1] / equity_series.iloc[0] - 1
    n_years = n / 252
    rolling_max = equity_series.cummax()
    drawdown = (equity_series - rolling_max) / rolling_max
    max_dd = drawdown.min()
    daily_returns = equity_series.pct_change().dropna()
    sharpe = (daily_returns.mean() / daily_returns.std() * np.sqrt(252)) if daily_returns.std() > 0 else 0

    # Buy & hold benchmark (first available ETF, equal-weight proxy)
    first_etf = next((c for c in etf_aligned.columns if c != "timestamp" and c in rotation["selected_etf"].values), None)
    if first_etf and first_etf in etf_aligned.columns:
        bh_prices = etf_aligned[first_etf].dropna()
        bh_return = (bh_prices.iloc[-1] / bh_prices.iloc[0] - 1) * 100 if len(bh_prices) > 1 else 0
        bh_equity = bh_prices / bh_prices.iloc[0]
        bh_peak = bh_equity.cummax()
        bh_max_dd = ((bh_equity - bh_peak) / bh_peak).min() * 100
    else:
        bh_return = 0.0
        bh_max_dd = 0.0

    # Win rate
    wins = sum(1 for t in trades if t["type"] == "exit")
    # Approximate: count profitable round-trips
    entry_equity = []
    exit_equity = []
    eq_idx = 0
    for t in trades:
        if t["type"] == "entry":
            entry_equity.append(equity[min(eq_idx, len(equity) - 1)])
        elif t["type"] == "exit":
            exit_equity.append(equity[min(eq_idx, len(equity) - 1)])
        eq_idx += 1
    profitable = sum(1 for e, x in zip(entry_equity, exit_equity) if x > e)
    win_rate = (profitable / len(exit_equity) * 100) if exit_equity else 0.0

    # Equity curve DataFrame
    timestamps = signal_layer["timestamp"].tolist()
    eq_df = pd.DataFrame({
        "timestamp": timestamps[:len(equity)],
        "equity": equity,
    })

    # Build Trade objects from raw trade records
    platform_trades = []
    open_entry = None
    for t in trades:
        if t["type"] == "entry":
            open_entry = t
        elif t["type"] == "exit" and open_entry is not None:
            entry_ts = pd.Timestamp(open_entry["date"])
            exit_ts = pd.Timestamp(t["date"])
            hold_bars = max(1, (exit_ts - entry_ts).days)
            # Find equity at entry/exit for PnL
            entry_eq_idx = next((j for j in range(len(timestamps)) if str(timestamps[j]) >= open_entry["date"]), 0)
            exit_eq_idx = next((j for j in range(len(timestamps)) if str(timestamps[j]) >= t["date"]), len(equity) - 1)
            entry_eq = equity[min(entry_eq_idx, len(equity) - 1)]
            exit_eq = equity[min(exit_eq_idx, len(equity) - 1)]
            pnl_pct = (exit_eq / entry_eq - 1) * 100 if entry_eq > 0 else 0
            platform_trades.append(Trade(
                entry_time=entry_ts,
                entry_price=1.0,
                exit_time=exit_ts,
                exit_price=1.0 * (1 + pnl_pct / 100),
                hold_bars=hold_bars,
                pnl_pct=round(pnl_pct, 4),
                pnl_abs=round(pnl_pct / 100, 6),
                asset=open_entry.get("etf", ""),
                status="closed",
            ))
            open_entry = None
        elif t["type"] == "rotation":
            # Rotation closes old position and opens new
            if open_entry is not None:
                entry_ts = pd.Timestamp(open_entry["date"])
                exit_ts = pd.Timestamp(t["date"])
                hold_bars = max(1, (exit_ts - entry_ts).days)
                entry_eq_idx = next((j for j in range(len(timestamps)) if str(timestamps[j]) >= open_entry["date"]), 0)
                exit_eq_idx = next((j for j in range(len(timestamps)) if str(timestamps[j]) >= t["date"]), len(equity) - 1)
                entry_eq = equity[min(entry_eq_idx, len(equity) - 1)]
                exit_eq = equity[min(exit_eq_idx, len(equity) - 1)]
                pnl_pct = (exit_eq / entry_eq - 1) * 100 if entry_eq > 0 else 0
                platform_trades.append(Trade(
                    entry_time=entry_ts,
                    entry_price=1.0,
                    exit_time=exit_ts,
                    exit_price=1.0 * (1 + pnl_pct / 100),
                    hold_bars=hold_bars,
                    pnl_pct=round(pnl_pct, 4),
                    pnl_abs=round(pnl_pct / 100, 6),
                    asset=open_entry.get("etf", ""),
                    status="closed",
                ))
            # New entry from rotation
            open_entry = {"date": t["date"], "etf": t.get("to", "")}

    # Determine if currently in position
    has_open = prev_risk_on and prev_etf != ""

    return BacktestResult(
        config=config,
        trades=platform_trades,
        equity_curve=eq_df,
        total_return_pct=round(total_return * 100, 2),
        realized_return_pct=round(total_return * 100, 2),
        max_drawdown_pct=round(max_dd * 100, 2),
        win_rate=round(win_rate, 1),
        total_trades=len(platform_trades),
        avg_hold_bars=round(n / max(len(platform_trades), 1), 1),
        sharpe_ratio=round(sharpe, 3),
        start_date=pd.Timestamp(timestamps[0]),
        end_date=pd.Timestamp(timestamps[-1]),
        buy_hold_return_pct=round(bh_return, 2),
        buy_hold_max_drawdown_pct=round(bh_max_dd, 2),
        has_open_position=has_open,
    )
