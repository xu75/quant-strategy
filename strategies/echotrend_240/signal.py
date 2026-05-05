from __future__ import annotations

"""EchoTrend 240 - Signal Logic (V6 Position Management)

Dual-feed strategy: BTC 4H SMA240 regime gate + V6 multi-factor scoring.
Architecture: v9 trend_regime variant from mstr-strategy-clowder.

Layer 0: V6 base target (trend/risk/RS scoring → 0.70–1.10)
Layer T: BTC 4H regime ceiling (bear=0.0, bull=1.10)
Final:   min(v6_target, regime_ceiling)
Execution: symmetric step with cooldown and reentry voting.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from strategies.echotrend_240.indicators import (
    filter_regular_hours,
    align_to_mstr_session,
    add_features,
    add_daily_trend_features,
    add_btc_4h_trend_features,
)
from pipeline.backtest import BacktestResult, Trade
from strategies.echotrend_240.engine import EchoTrendEngine, EngineBacktestResult

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
    bull_ceiling: float = 1.00
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
    action: str  # "buy", "sell", "hold", "adjust"
    price: float
    ma_value: float
    timestamp: pd.Timestamp
    hold_bars: int = 0
    reason: str = ""
    regime: str = ""
    target_exposure: float = 0.0
    mode: str = ""
    scores: dict = field(default_factory=dict)


def _build_engine_config(config: StrategyConfig) -> dict:
    """Build engine config dict from StrategyConfig + frozen V6/V9 params."""
    return {
        "rsi_window": 14,
        "costs": {
            "commission_rate": config.commission_rate,
            "slippage_rate": config.slippage_rate,
        },
        "v6": {
            "min_exposure": 0.70,
            "max_exposure": 1.00,
            "max_margin_fraction": 0.00,
            "base_exposure": 1.00,
            "trend_weight": 0.12,
            "risk_weight": 0.40,
            "relative_strength_weight": 0.07,
            "target_ema_alpha": 0.30,
            "min_trade_exposure": 0.035,
            "cooldown_bars": 39,
            "max_step_up": 0.55,
            "max_step_down": 0.50,
            "fast_reentry_step": 0.90,
            "crash_step_down": 0.80,
            "mode_confirm_bars": 6,
            "risk_mode_confirm_bars": 3,
            "bull_mode_confirm_bars": 8,
            "mode_floors": {"bull": 0.98, "neutral": 0.90, "risk_off": 0.72, "crash": 0.70},
            "mode_ceilings": {"bull": 1.00, "neutral": 1.00, "risk_off": 0.88, "crash": 0.76},
            "mstr_1h_pos": 0.010, "mstr_4h_pos": 0.020,
            "mstr_5d_pos": 0.030, "mstr_20d_pos": 0.050,
            "btc_20d_pos": 0.030, "market_1h_pos": 0.000,
            "mstr_1h_risk": -0.035, "mstr_4h_risk": -0.060, "mstr_5d_risk": -0.100,
            "btc_1h_risk": -0.020, "btc_5d_risk": -0.060,
            "market_1h_risk": -0.008, "market_5d_risk": -0.030,
            "rs_4h_risk": -0.040, "day_range_risk": 0.090,
            "rs_1h_pos": 0.010, "rs_4h_pos": 0.020, "ratio_20_pos": 0.030,
            "rs_1h_neg": -0.020, "rs_4h_neg": -0.040, "ratio_20_neg": -0.050,
            "dip_min_trend_score": 55, "dip_max_risk_score": 45,
            "dip_vwap_dev": 0.010, "dip_add": 0.030,
            "breakout_add": 0.030,
            "overheat_max_risk_score": 50, "overheat_rsi": 82,
            "overheat_vwap_dev": 0.040, "overheat_reduce": 0.030,
            "max_intraday_offset": 0.050,
            "crash_risk_score": 70, "risk_off_score": 45,
            "bull_exposure_threshold": 1.01,
            "reentry_mstr_1h": 0.035, "reentry_btc_1h": 0.010,
            "reentry_market_1h": 0.003, "reentry_rs_1h": 0.010,
            "reentry_min_votes": 3,
        },
        "v9": {
            "min_exposure": 0.0,
            "trend_regime": {
                "ma_field": f"btc_4h_above_sma{config.ma_window}",
                "bear_ceiling": config.bear_ceiling,
                "bull_ceiling": config.bull_ceiling,
                "bear_confirm_bars": config.bear_confirm_bars,
                "bull_confirm_bars": config.bull_confirm_bars,
                "hysteresis_pct": config.hysteresis_pct,
                "freeze_bars": config.freeze_bars,
            },
        },
    }


def _prepare_features(
    df: pd.DataFrame,
    config: StrategyConfig,
    *,
    extra_data: dict | None = None,
) -> pd.DataFrame:
    """Build the full feature DataFrame from raw data sources."""
    extra_data = extra_data or {}
    btc_df = extra_data.get("btc")
    qqq_df = extra_data.get("qqq")
    mstr_daily = extra_data.get("mstr_daily")
    btc_daily = extra_data.get("btc_daily")
    qqq_daily = extra_data.get("qqq_daily")

    if btc_df is None:
        raise ValueError("btc_df is required for EchoTrend 240")
    if qqq_df is None:
        raise ValueError("qqq_df is required for EchoTrend 240")
    if mstr_daily is None:
        raise ValueError("mstr_daily is required for EchoTrend 240")
    if btc_daily is None:
        raise ValueError("btc_daily is required for EchoTrend 240")
    if qqq_daily is None:
        raise ValueError("qqq_daily is required for EchoTrend 240")

    mstr_rth = filter_regular_hours(df) if config.regular_hours_only else df.copy()
    mstr_rth, btc_aligned, qqq_aligned = align_to_mstr_session(mstr_rth, btc_df, qqq_df)

    features = add_features(mstr_rth, btc_aligned, qqq_aligned, rsi_window=14)

    features = add_daily_trend_features(
        features, mstr_daily, btc_daily, qqq_daily,
    )

    features = add_btc_4h_trend_features(features, btc_df, ma_period=config.ma_window)

    return features


def compute_signals(
    df: pd.DataFrame,
    config: StrategyConfig | None = None,
    *,
    extra_data: dict | None = None,
) -> list[Signal]:
    """Compute trading signals using V6 engine.

    Returns a list of Signal objects for each rebalance event.
    The backtest is run internally by the engine; signals are extracted
    from the engine's rebalance log.
    """
    if config is None:
        config = StrategyConfig()

    features = _prepare_features(df, config, extra_data=extra_data)

    engine_config = _build_engine_config(config)
    engine = EchoTrendEngine(engine_config)
    result = engine.run_backtest(features)

    signals: list[Signal] = []
    for rb in result.rebalances:
        action = "buy" if rb.side == "buy" else "sell"
        signals.append(Signal(
            action=action,
            price=rb.price,
            ma_value=0.0,
            timestamp=rb.timestamp,
            reason=rb.reason,
            regime="bull" if engine.trend_regime == "bull" else "bear",
            target_exposure=rb.target_exposure,
            mode=rb.mode,
            scores=rb.scores,
        ))

    return signals


def get_current_signal(
    df: pd.DataFrame,
    in_position: bool,
    entry_bar_idx: int = 0,
    config: StrategyConfig | None = None,
    *,
    extra_data: dict | None = None,
) -> Signal:
    """Get the current signal state from the engine."""
    if config is None:
        config = StrategyConfig()

    features = _prepare_features(df, config, extra_data=extra_data)

    if features.empty or len(features) < 3:
        return Signal(
            action="hold", price=0.0, ma_value=0.0,
            timestamp=pd.Timestamp.now(tz="UTC"),
            reason="Insufficient data",
        )

    engine_config = _build_engine_config(config)
    engine = EchoTrendEngine(engine_config)
    result = engine.run_backtest(features)

    last_row = features.iloc[-1]
    price = float(last_row["close"])

    action = "hold"
    reason = f"Exposure {result.final_exposure:.2f}, mode={result.final_mode}, regime={result.final_regime}"

    if result.rebalances:
        last_rb = result.rebalances[-1]
        last_ts = features.index[-1]
        if last_rb.timestamp == last_ts:
            action = last_rb.side
            reason = last_rb.reason

    return Signal(
        action=action,
        price=price,
        ma_value=0.0,
        timestamp=features.index[-1],
        reason=reason,
        regime=result.final_regime,
        target_exposure=result.final_exposure,
        mode=result.final_mode,
        scores=engine.last_scores,
    )


def get_filtered_df(
    df: pd.DataFrame,
    config: StrategyConfig | None = None,
) -> pd.DataFrame:
    """Return MSTR data filtered to regular hours for use by the runner."""
    if config is None:
        config = StrategyConfig()
    if config.regular_hours_only:
        return filter_regular_hours(df)
    return df.copy()


def run_backtest(
    df: pd.DataFrame,
    config: StrategyConfig | None = None,
    *,
    extra_data: dict | None = None,
    initial_capital: float = 10000.0,
    fee_rate: float = 0.001,
) -> BacktestResult:
    """Run engine-native backtest and return platform-compatible BacktestResult.

    This bypasses the binary buy/sell backtest in pipeline.backtest,
    using the V6 engine's continuous exposure management directly.
    """
    if config is None:
        config = StrategyConfig()

    features = _prepare_features(df, config, extra_data=extra_data)

    engine_config = _build_engine_config(config)
    engine = EchoTrendEngine(engine_config)
    result = engine.run_backtest(features, initial_capital=initial_capital)

    trades = _engine_rebalances_to_trades(result.rebalances)

    total_trades = len(trades)
    wins = sum(1 for t in trades if t.pnl_pct > 0)
    win_rate = wins / total_trades * 100 if total_trades > 0 else 0.0
    avg_hold = sum(t.hold_bars for t in trades) / total_trades if total_trades > 0 else 0.0

    return BacktestResult(
        config=config,
        trades=trades,
        equity_curve=result.equity_curve,
        total_return_pct=result.total_return_pct,
        realized_return_pct=result.total_return_pct,
        max_drawdown_pct=result.max_drawdown_pct,
        win_rate=win_rate,
        total_trades=total_trades,
        avg_hold_bars=avg_hold,
        sharpe_ratio=result.sharpe_ratio,
        start_date=result.start_date,
        end_date=result.end_date,
        buy_hold_return_pct=result.buy_hold_return_pct,
        buy_hold_max_drawdown_pct=result.buy_hold_max_drawdown_pct,
        has_open_position=result.final_exposure > 0.01,
    )


def _engine_rebalances_to_trades(rebalances: list) -> list[Trade]:
    """Convert engine rebalance events into platform Trade objects.

    Groups buy→sell pairs into round-trip trades for compatibility
    with period metrics and reporting.
    """
    trades: list[Trade] = []
    open_buy = None
    for rb in rebalances:
        if rb.side == "buy" and open_buy is None:
            open_buy = rb
        elif rb.side == "sell" and open_buy is not None:
            pnl_pct = (rb.price - open_buy.price) / open_buy.price * 100
            pnl_abs = (rb.price - open_buy.price) * rb.qty
            hold_bars = 0
            trades.append(Trade(
                entry_time=open_buy.timestamp,
                entry_price=open_buy.price,
                exit_time=rb.timestamp,
                exit_price=rb.price,
                hold_bars=hold_bars,
                pnl_pct=pnl_pct,
                pnl_abs=pnl_abs,
            ))
            open_buy = None
    return trades


def export_engine_state(
    df: pd.DataFrame,
    config: StrategyConfig | None = None,
    *,
    extra_data: dict | None = None,
    initial_capital: float = 10000.0,
) -> dict:
    """Run full engine and export final state snapshot for daily-signal resume."""
    if config is None:
        config = StrategyConfig()

    features = _prepare_features(df, config, extra_data=extra_data)
    engine_config = _build_engine_config(config)
    engine = EchoTrendEngine(engine_config)

    first_price = float(features.iloc[0]["close"])
    state = engine.initial_state(first_price, initial_capital=initial_capital)
    for i in range(len(features)):
        engine.on_bar(state, features.index[i], features.iloc[i])

    watermark = features.index[-1]
    return engine.export_state(state, watermark)


def run_incremental(
    df: pd.DataFrame,
    config: StrategyConfig | None = None,
    saved_state: dict = None,
    *,
    extra_data: dict | None = None,
) -> dict | None:
    """Resume engine from saved state, process only new bars.

    Returns dict with keys: watermark, exposure, mode, regime, state, current_signal.
    Returns None if no new bars to process.
    """
    if config is None:
        config = StrategyConfig()
    if saved_state is None:
        return None

    features = _prepare_features(df, config, extra_data=extra_data)
    engine_config = _build_engine_config(config)
    engine = EchoTrendEngine(engine_config)
    state = engine.import_state(saved_state)

    watermark = pd.Timestamp(saved_state["watermark"])
    if watermark.tzinfo is None:
        watermark = watermark.tz_localize("UTC")

    new_bars = features.loc[features.index > watermark]
    if new_bars.empty:
        return None

    for i in range(len(new_bars)):
        engine.on_bar(state, new_bars.index[i], new_bars.iloc[i])

    new_watermark = features.index[-1]
    exposure = state.total_shares / state.initial_shares
    snapshot = engine.export_state(state, new_watermark)

    last_row = features.iloc[-1]
    price = float(last_row["close"])
    reason = f"Exposure {exposure:.2f}, mode={engine.mode}, regime={engine.trend_regime}"
    action = "hold"
    if engine.rebalances:
        last_rb = engine.rebalances[-1]
        if last_rb.timestamp == new_watermark:
            action = last_rb.side
            reason = last_rb.reason

    current_signal = Signal(
        action=action,
        price=price,
        ma_value=0.0,
        timestamp=new_watermark,
        reason=reason,
        regime=engine.trend_regime,
        target_exposure=exposure,
        mode=engine.mode,
        scores=engine.last_scores,
    )

    return {
        "watermark": new_watermark,
        "exposure": exposure,
        "mode": engine.mode,
        "regime": engine.trend_regime,
        "state": snapshot,
        "current_signal": current_signal,
    }
