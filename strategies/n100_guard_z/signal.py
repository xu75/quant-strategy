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
    action: str  # "buy" / "sell" / "hold"
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

    # Build signal list using execution calendar state machine.
    # The execution calendar = common_dates (US ∩ A-share ∩ NAV), sorted.
    # Position only changes on execution days (next A-share day after signal).
    # Between execution days, holding is frozen at last executed state.
    #
    # State machine mirrors run_backtest:
    #   signal_by_date[d].selected_etf = rotation[d-1] (shift(1) in common_dates)
    #   Transition happens at exec_day (the NEXT common_date after signal fires)
    #   So on signal day D, we are still holding the PREVIOUS position.
    #   The new position only becomes active on the next common_date after D.

    # Pre-compute execution calendar signal state (same as run_backtest)
    common_sorted = sorted(common_dates)
    n_common = len(common_sorted)

    # Build the same signal_by_date as run_backtest (shift(1) on selected_etf)
    exec_signal_by_date = {}
    for idx in range(n_common):
        d = signal_aligned["timestamp"].iloc[idx]
        shifted_etf = rotation["selected_etf"].iloc[idx - 1] if idx > 0 else ""
        exec_signal_by_date[d] = {
            "risk_on": bool(signal_aligned["risk_on"].iloc[idx]),
            "selected_etf": shifted_etf,
        }

    # Walk the execution calendar to determine actual holding at each exec_day
    # holding_at_date[d] = ETF code actually held as of date d's open
    holding_at_date = {}
    exec_prev_risk_on = False
    exec_prev_etf = ""
    exec_holding = ""  # what we are actually holding right now

    for i in range(1, n_common - 1):
        sig_day = common_sorted[i]
        exec_day = common_sorted[i + 1]

        sig = exec_signal_by_date.get(sig_day)
        if sig is None:
            holding_at_date[exec_day] = exec_holding
            continue

        curr_risk_on = sig["risk_on"]
        curr_etf = sig["selected_etf"]
        effective_risk_on = curr_risk_on and curr_etf and curr_etf in etf_aligned.columns

        is_entry = effective_risk_on and not exec_prev_risk_on
        is_exit = not effective_risk_on and exec_prev_risk_on and exec_prev_etf != ""
        is_rotation = (effective_risk_on and exec_prev_risk_on
                       and exec_prev_etf != "" and exec_prev_etf != curr_etf)

        if is_entry:
            exec_holding = curr_etf
        elif is_exit:
            exec_holding = ""
        elif is_rotation:
            exec_holding = curr_etf
        # else: hold — no change

        holding_at_date[exec_day] = exec_holding
        exec_prev_risk_on = effective_risk_on
        exec_prev_etf = curr_etf if effective_risk_on else ""

    # Also set initial days (before first exec) as empty
    if n_common > 1:
        holding_at_date[common_sorted[0]] = ""
        holding_at_date[common_sorted[1]] = ""

    # Now build signals for ALL signal_layer dates (including non-A-share US dates).
    # For each date, find the most recent execution state.
    signals = []
    last_zscore = 0.0
    last_premium = 0.0
    # Carry-forward: find the latest common_date <= row_ts to get current holding
    common_idx = 0
    current_holding = ""

    for i in range(len(signal_layer)):
        row = signal_layer.iloc[i]
        row_ts = row["timestamp"]
        etf_info = rotation_by_ts.loc[row_ts] if row_ts in rotation_by_ts.index else None

        if etf_info is not None:
            last_zscore = etf_info["zscore"]
            last_premium = etf_info["premium"]

        # Advance execution calendar pointer to find current holding
        while (common_idx < n_common - 1
               and common_sorted[common_idx + 1] <= row_ts):
            common_idx += 1
        if common_sorted[common_idx] <= row_ts and common_sorted[common_idx] in holding_at_date:
            current_holding = holding_at_date[common_sorted[common_idx]]

        is_risk_on = bool(row["risk_on"])
        # current_etf = actual execution state (what we're holding NOW).
        # On exit signal day, sell hasn't executed yet — still holding old ETF.
        # action="sell" communicates the pending action separately.
        etf_code = current_holding

        # Action based on risk_on transitions (for display purposes)
        prev_risk = bool(signal_layer.iloc[i - 1]["risk_on"]) if i > 0 else False
        if is_risk_on and not prev_risk:
            action = "buy"
        elif not is_risk_on and prev_risk:
            action = "sell"
        else:
            action = "hold"

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
            current_zscore=last_zscore if is_risk_on else 0.0,
            current_premium=last_premium if is_risk_on else 0.0,
        )
        signals.append(sig)

    return signals


def get_current_signal(df: pd.DataFrame, in_position: bool = False,
                       entry_bar_idx: int = 0, config: StrategyConfig = None,
                       **kwargs) -> Signal:
    """Return signal for the latest bar."""
    signals = compute_signals(df, config, **kwargs)
    if not signals:
        return Signal(action="hold", reason="no_data")
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

    # --- T+1 Execution Model ---
    # Signal on day[i] (US close, after A-share close) → execute at day[i+1] open
    # In common_dates (US ∩ A-share ∩ NAV), consecutive bars are consecutive
    # A-share trading days. Holidays are naturally skipped.
    # This ensures that when US signals on 09-30 (last A-share day before National Day),
    # execution happens on 10-09 (first A-share day after holiday), not on 09-30 itself.

    # Signal generation still uses common_dates for z-score/rotation computation
    common_signal_dates = set(signal_layer["timestamp"]) & set(etf_df["timestamp"]) & set(nav_df["timestamp"])
    signal_layer = signal_layer[signal_layer["timestamp"].isin(common_signal_dates)].reset_index(drop=True)
    etf_aligned = etf_df[etf_df["timestamp"].isin(common_signal_dates)].sort_values("timestamp").reset_index(drop=True)
    nav_aligned = nav_df[nav_df["timestamp"].isin(common_signal_dates)].sort_values("timestamp").reset_index(drop=True)

    n = len(signal_layer)
    qqq_ret_aligned = signal_layer["qqq_close"].pct_change().fillna(0)

    # Rotation layer (uses close prices for z-score/premium — signal generation only)
    risk_on_mask = signal_layer["risk_on"]
    rotation = _zscore_rotation(etf_aligned, nav_aligned, qqq_ret_aligned, config, risk_on_mask)

    # Build signal lookup: date → (risk_on, selected_etf)
    # risk_on uses current day's signal (immediate T+1 execution for exits)
    # selected_etf uses shift(1) — position = selected.shift(1) per local project
    # This gives: rotation signal on D → position active D+1 → exec D+2
    #             risk_off signal on D → exec D+1 (immediate protective exit)
    signal_by_date = {}
    for idx in range(n):
        d = signal_layer["timestamp"].iloc[idx]
        shifted_etf = rotation["selected_etf"].iloc[idx - 1] if idx > 0 else ""
        signal_by_date[d] = {
            "risk_on": bool(signal_layer["risk_on"].iloc[idx]),
            "selected_etf": shifted_etf,
        }

    # Full A-share DataFrames indexed by date for execution price lookup
    etf_full = etf_df.sort_values("timestamp").set_index("timestamp")
    open_full = etf_open_df.sort_values("timestamp").set_index("timestamp") if etf_open_df is not None else None
    adj_full = etf_adj_df.sort_values("timestamp").set_index("timestamp") if etf_adj_df is not None else None

    # A-share trading days that also have signal data (common_dates, sorted)
    ashare_signal_days = sorted(common_signal_dates)
    m = len(ashare_signal_days)

    def _get_adj_open_by_date(exec_date, etf_code):
        """Get adjusted open price for a specific A-share execution date."""
        if exec_date is None:
            return None
        if (open_full is not None and adj_full is not None
                and etf_code in open_full.columns and etf_code in adj_full.columns
                and etf_code in etf_full.columns):
            if exec_date in open_full.index and exec_date in adj_full.index and exec_date in etf_full.index:
                o = open_full.at[exec_date, etf_code]
                adj_c = adj_full.at[exec_date, etf_code]
                c = etf_full.at[exec_date, etf_code]
                if pd.notna(o) and pd.notna(adj_c) and pd.notna(c) and c > 0:
                    return o * (adj_c / c)
        # Fallback: use close as proxy
        if etf_code in etf_full.columns and exec_date in etf_full.index:
            v = etf_full.at[exec_date, etf_code]
            if pd.notna(v):
                return v
        return None

    # Backtest engine — T+1 open execution
    # signal_by_date has shift(1) on selected_etf but not risk_on.
    # Signal on day[i] → execute at day[i+1] open.
    # Rotation: selected_etf shift(1) means rotation detected one day later,
    #   so entry exec = day[i+1] which is 2 bars after original selection.
    # Exit: risk_off is immediate, exec = day[i+1] (1 bar after signal).
    equity = [1.0]
    trades = []
    daily_mm_rate = config.money_market_annual_rate / 365

    prev_risk_on = False
    prev_etf = ""

    for i in range(1, m - 1):
        sig_day = ashare_signal_days[i]
        exec_day = ashare_signal_days[i + 1]

        sig = signal_by_date.get(sig_day)
        if sig is None:
            equity.append(equity[-1] * (1 + daily_mm_rate))
            continue

        curr_risk_on = sig["risk_on"]
        curr_etf = sig["selected_etf"]
        daily_return = 0.0

        effective_risk_on = curr_risk_on and curr_etf and curr_etf in etf_full.columns

        is_entry = effective_risk_on and not prev_risk_on
        is_exit = not effective_risk_on and prev_risk_on and prev_etf != ""
        is_rotation = (effective_risk_on and prev_risk_on
                       and prev_etf != "" and prev_etf != curr_etf)
        is_hold = effective_risk_on and prev_risk_on and prev_etf == curr_etf

        if is_entry:
            daily_return = daily_mm_rate
            daily_return -= config.fee_rate
            trades.append({
                "type": "entry",
                "etf": curr_etf,
                "exec_date": exec_day,
            })
        elif is_exit:
            # Exit: sell at exec_day open. Last holding return = open[exec_day]/open[sig_day]
            exec_price_today = _get_adj_open_by_date(exec_day, prev_etf)
            exec_price_yesterday = _get_adj_open_by_date(sig_day, prev_etf)
            if exec_price_today is not None and exec_price_yesterday is not None and exec_price_yesterday > 0:
                daily_return = exec_price_today / exec_price_yesterday - 1
            daily_return -= config.fee_rate
            trades.append({
                "type": "exit",
                "etf": prev_etf,
                "exec_date": exec_day,
            })
        elif is_rotation:
            # Rotation: sell old at exec_day open, buy new at exec_day open
            exec_price_today = _get_adj_open_by_date(exec_day, prev_etf)
            exec_price_yesterday = _get_adj_open_by_date(sig_day, prev_etf)
            if exec_price_today is not None and exec_price_yesterday is not None and exec_price_yesterday > 0:
                daily_return = exec_price_today / exec_price_yesterday - 1
            daily_return -= 2 * config.fee_rate
            trades.append({
                "type": "rotation",
                "from": prev_etf,
                "to": curr_etf,
                "exec_date": exec_day,
            })
        elif is_hold:
            exec_price_today = _get_adj_open_by_date(exec_day, curr_etf)
            exec_price_yesterday = _get_adj_open_by_date(sig_day, curr_etf)
            if exec_price_today is not None and exec_price_yesterday is not None and exec_price_yesterday > 0:
                daily_return = exec_price_today / exec_price_yesterday - 1
        else:
            daily_return = daily_mm_rate

        equity.append(equity[-1] * (1 + daily_return))
        prev_risk_on = effective_risk_on
        prev_etf = curr_etf if effective_risk_on else ""

    # Metrics
    equity_series = pd.Series(equity)
    total_return = equity_series.iloc[-1] / equity_series.iloc[0] - 1
    n_years = m / 252
    rolling_max = equity_series.cummax()
    drawdown = (equity_series - rolling_max) / rolling_max
    max_dd = drawdown.min()
    daily_returns = equity_series.pct_change().dropna()
    sharpe = (daily_returns.mean() / daily_returns.std() * np.sqrt(252)) if daily_returns.std() > 0 else 0

    # Buy & hold benchmark: 513100 (国泰纳指100, longest history)
    # Clipped to backtest window for matching comparison
    bh_etf = "513100"
    bt_start = ashare_signal_days[0]
    bt_end = ashare_signal_days[-1]
    if bh_etf in etf_full.columns:
        bh_prices = etf_full[bh_etf].loc[bt_start:bt_end].dropna()
        bh_return = (bh_prices.iloc[-1] / bh_prices.iloc[0] - 1) * 100 if len(bh_prices) > 1 else 0
        bh_equity = bh_prices / bh_prices.iloc[0]
        bh_peak = bh_equity.cummax()
        bh_max_dd = ((bh_equity - bh_peak) / bh_peak).min() * 100
    else:
        bh_return = 0.0
        bh_max_dd = 0.0

    # Equity curve DataFrame
    # equity[0] = initial value (start of backtest)
    # equity[k] for k>=1 = value after iteration i=k, realized at exec_day = days[k+1]
    # Timestamps: equity[0] → days[0] (start), equity[k] → days[k+1] (exec_day)
    eq_timestamps = [ashare_signal_days[0]]  # initial
    for i in range(1, m - 1):
        if i < len(equity):
            eq_timestamps.append(ashare_signal_days[i + 1])
    eq_df = pd.DataFrame({
        "timestamp": eq_timestamps[:len(equity)],
        "equity": equity,
    })

    # Build Trade objects — uses actual A-share execution dates and prices
    platform_trades = []
    open_entry = None
    for t in trades:
        if t["type"] == "entry":
            open_entry = t
        elif t["type"] == "exit" and open_entry is not None:
            entry_exec = open_entry["exec_date"]
            exit_exec = t["exec_date"]
            entry_ts = pd.Timestamp(entry_exec)
            exit_ts = pd.Timestamp(exit_exec)
            hold_bars = max(1, (exit_ts - entry_ts).days)
            entry_etf = open_entry.get("etf", "")
            actual_entry = _get_adj_open_by_date(entry_exec, entry_etf)
            actual_exit = _get_adj_open_by_date(exit_exec, entry_etf)
            if actual_entry and actual_entry > 0 and actual_exit:
                pnl_pct = (actual_exit / actual_entry - 1 - 2 * config.fee_rate) * 100
            else:
                pnl_pct = 0.0
            platform_trades.append(Trade(
                entry_time=entry_ts,
                entry_price=round(actual_entry or 1.0, 4),
                exit_time=exit_ts,
                exit_price=round(actual_exit or 1.0, 4),
                hold_bars=hold_bars,
                pnl_pct=round(pnl_pct, 4),
                pnl_abs=round(pnl_pct / 100, 6),
                asset=entry_etf,
                status="closed",
            ))
            open_entry = None
        elif t["type"] == "rotation":
            if open_entry is not None:
                entry_exec = open_entry["exec_date"]
                exit_exec = t["exec_date"]
                entry_ts = pd.Timestamp(entry_exec)
                exit_ts = pd.Timestamp(exit_exec)
                hold_bars = max(1, (exit_ts - entry_ts).days)
                entry_etf = open_entry.get("etf", "")
                actual_entry = _get_adj_open_by_date(entry_exec, entry_etf)
                actual_exit = _get_adj_open_by_date(exit_exec, entry_etf)
                if actual_entry and actual_entry > 0 and actual_exit:
                    pnl_pct = (actual_exit / actual_entry - 1 - 2 * config.fee_rate) * 100
                else:
                    pnl_pct = 0.0
                platform_trades.append(Trade(
                    entry_time=entry_ts,
                    entry_price=round(actual_entry or 1.0, 4),
                    exit_time=exit_ts,
                    exit_price=round(actual_exit or 1.0, 4),
                    hold_bars=hold_bars,
                    pnl_pct=round(pnl_pct, 4),
                    pnl_abs=round(pnl_pct / 100, 6),
                    asset=entry_etf,
                    status="closed",
                ))
            open_entry = {"type": "entry", "etf": t.get("to", ""), "exec_date": t["exec_date"]}

    # Determine if currently in position
    has_open = prev_risk_on and prev_etf != ""

    # Win rate from actual ETF price PnL
    profitable = sum(1 for t in platform_trades if t.pnl_pct > 0)
    win_rate = (profitable / len(platform_trades) * 100) if platform_trades else 0.0

    return BacktestResult(
        config=config,
        trades=platform_trades,
        equity_curve=eq_df,
        total_return_pct=round(total_return * 100, 2),
        realized_return_pct=round(total_return * 100, 2),
        max_drawdown_pct=round(max_dd * 100, 2),
        win_rate=round(win_rate, 1),
        total_trades=len(platform_trades),
        avg_hold_bars=round(m / max(len(platform_trades), 1), 1),
        sharpe_ratio=round(sharpe, 3),
        start_date=pd.Timestamp(ashare_signal_days[0]),
        end_date=pd.Timestamp(ashare_signal_days[-1]),
        buy_hold_return_pct=round(bh_return, 2),
        buy_hold_max_drawdown_pct=round(bh_max_dd, 2),
        has_open_position=has_open,
    )
