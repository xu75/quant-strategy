from __future__ import annotations

"""EchoTrend 240 V3 - Signal Logic (Decision-Row Execution + CB + Crash Confirm)

Key changes from V2:
- Decision-row execution: current-bar open (BTC 4H via shift(1)+ffill already known)
- Asymmetric confirm: bear=3, bull=9 (MSTR 1H bars)
- Circuit breaker: RTH intraday -10% drop → target=0, gap*step execution
- Crash mode: 1-bar confirm, step_down=0.80
- Strong reentry: 3/5 votes → step_up=0.90
- Cooldown: 0 (removed)
- Position sizing: current equity
- All execution via gap*step (no force_exit): sell step=0.50/0.80, buy step=0.55/0.90
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from strategies.echotrend_240_v3.indicators import (
    filter_regular_hours,
    align_to_mstr_session,
    add_features,
    add_daily_trend_features,
    add_btc_4h_trend_features,
)
from pipeline.backtest import BacktestResult, Trade
from strategies.echotrend_240_v3.engine import EchoTrendV3Engine, EngineBacktestResult

MARKET_TZ = "US/Eastern"
MARKET_OPEN = "09:30"
MARKET_CLOSE = "16:00"


@dataclass
class StrategyConfig:
    display_name: str = "EchoTrend 240 V3"
    internal_code: str = "ET240V3"
    ma_window: int = 240
    bear_confirm_bars: int = 3
    bull_confirm_bars: int = 9
    bear_ceiling: float = 0.0
    bull_ceiling: float = 1.00
    hysteresis_pct: float = 0.0
    freeze_bars: int = 0
    commission_rate: float = 0.0005
    slippage_rate: float = 0.0005
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
    action: str
    price: float
    ma_value: float
    timestamp: pd.Timestamp
    hold_bars: int = 0
    reason: str = ""
    regime: str = ""
    target_exposure: float = 0.0        # TRUE strategy target (bear=0, bull=1, CB=0)
    current_exposure: float = 0.0       # executed model position (what to hold now)
    exposure_state: str = ""            # reducing / increasing / holding
    mode: str = ""
    scores: dict = field(default_factory=dict)


# Minimum gap before we call the position "increasing"/"reducing" rather than
# "holding". Matches the engine's min_trade_exposure so display and execution agree.
EXPOSURE_STATE_EPS = 0.035


def exposure_state(current: float, target: float, eps: float = EXPOSURE_STATE_EPS) -> str:
    """Classify the model's intent from executed exposure vs strategy target.

    - reducing:   target below current by at least eps (winding position down)
    - increasing: target above current by at least eps (building position up)
    - holding:    within eps of target (at destination)

    Boundary uses >= / <= to match the engine, which trades when the gap is
    exactly min_trade_exposure (it only *skips* when abs(gap) < min_trade_exposure).
    """
    if current is None or target is None:
        return ""
    gap = target - current
    if gap >= eps:
        return "increasing"
    if gap <= -eps:
        return "reducing"
    return "holding"


def _build_engine_config(config: StrategyConfig) -> dict:
    return {
        "rsi_window": 14,
        "costs": {
            "commission_rate": config.commission_rate,
            "slippage_rate": config.slippage_rate,
        },
        "v6": {
            "min_exposure": 1.00,
            "max_exposure": 1.00,
            "base_exposure": 1.00,
            "trend_weight": 0.12,
            "risk_weight": 0.40,
            "relative_strength_weight": 0.07,
            "target_ema_alpha": 0.30,
            "min_trade_exposure": 0.035,
            "max_step_up": 0.55,
            "max_step_down": 0.50,
            "fast_reentry_step": 0.90,
            "crash_step_down": 0.80,
            "mode_confirm_bars": 6,
            "risk_mode_confirm_bars": 1,
            "bull_mode_confirm_bars": 8,
            "mode_floors": {"bull": 1.00, "neutral": 1.00, "risk_off": 1.00, "crash": 1.00},
            "mode_ceilings": {"bull": 1.00, "neutral": 1.00, "risk_off": 1.00, "crash": 1.00},
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
            "circuit_breaker": {
                "intraday_drop": -0.10,
                "reset_next_day": True,
            },
        },
    }


def _prepare_features(
    df: pd.DataFrame,
    config: StrategyConfig,
    *,
    extra_data: dict | None = None,
) -> pd.DataFrame:
    extra_data = extra_data or {}
    btc_df = extra_data.get("btc")
    qqq_df = extra_data.get("qqq")
    mstr_daily = extra_data.get("mstr_daily")
    btc_daily = extra_data.get("btc_daily")
    qqq_daily = extra_data.get("qqq_daily")

    if btc_df is None:
        raise ValueError("btc_df is required for EchoTrend 240 V3")
    if qqq_df is None:
        raise ValueError("qqq_df is required for EchoTrend 240 V3")
    if mstr_daily is None:
        raise ValueError("mstr_daily is required for EchoTrend 240 V3")
    if btc_daily is None:
        raise ValueError("btc_daily is required for EchoTrend 240 V3")
    if qqq_daily is None:
        raise ValueError("qqq_daily is required for EchoTrend 240 V3")

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
    if config is None:
        config = StrategyConfig()

    features = _prepare_features(df, config, extra_data=extra_data)

    engine_config = _build_engine_config(config)
    engine = EchoTrendV3Engine(engine_config)
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
    engine = EchoTrendV3Engine(engine_config)
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
        target_exposure=result.final_target_exposure,
        current_exposure=result.final_exposure,
        exposure_state=exposure_state(result.final_exposure, result.final_target_exposure),
        mode=result.final_mode,
        scores=engine.last_scores,
    )


def get_filtered_df(
    df: pd.DataFrame,
    config: StrategyConfig | None = None,
) -> pd.DataFrame:
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
    if config is None:
        config = StrategyConfig()

    features = _prepare_features(df, config, extra_data=extra_data)

    engine_config = _build_engine_config(config)
    engine = EchoTrendV3Engine(engine_config)
    result = engine.run_backtest(features, initial_capital=initial_capital)

    trades = _engine_rebalances_to_trades(result.rebalances)

    total_trades = len(trades)
    wins = sum(1 for t in trades if t.pnl_pct > 0)
    win_rate = wins / total_trades * 100 if total_trades > 0 else 0.0
    avg_hold = sum(t.hold_bars for t in trades) / total_trades if total_trades > 0 else 0.0

    has_open = result.final_exposure > 0.01
    if has_open and trades:
        realized_pct = _compute_realized_return(trades, result, initial_capital)
    else:
        realized_pct = result.total_return_pct

    return BacktestResult(
        config=config,
        trades=trades,
        equity_curve=result.equity_curve,
        total_return_pct=result.total_return_pct,
        realized_return_pct=realized_pct,
        max_drawdown_pct=result.max_drawdown_pct,
        win_rate=win_rate,
        total_trades=total_trades,
        avg_hold_bars=avg_hold,
        sharpe_ratio=result.sharpe_ratio,
        start_date=result.start_date,
        end_date=result.end_date,
        buy_hold_return_pct=result.buy_hold_return_pct,
        buy_hold_max_drawdown_pct=result.buy_hold_max_drawdown_pct,
        has_open_position=has_open,
    )


def _compute_realized_return(trades: list[Trade], result, initial_capital: float) -> float:
    if not trades:
        return 0.0
    last_exit = trades[-1].exit_time
    eq = result.equity_curve
    mask = eq["timestamp"] <= last_exit
    if not mask.any():
        return 0.0
    realized_equity = float(eq.loc[mask, "equity"].iloc[-1])
    return round((realized_equity - initial_capital) / initial_capital * 100, 2)


def _engine_rebalances_to_trades(rebalances: list) -> list[Trade]:
    trades: list[Trade] = []
    open_buy = None
    for rb in rebalances:
        if rb.side == "buy" and open_buy is None:
            open_buy = rb
        elif rb.side == "sell" and open_buy is not None:
            pnl_pct = (rb.price - open_buy.price) / open_buy.price * 100
            pnl_abs = (rb.price - open_buy.price) * rb.qty
            delta = rb.timestamp - open_buy.timestamp
            hold_bars = max(1, int(delta.total_seconds() / 3600))
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
    if config is None:
        config = StrategyConfig()

    features = _prepare_features(df, config, extra_data=extra_data)
    engine_config = _build_engine_config(config)
    engine = EchoTrendV3Engine(engine_config)

    first_price = float(features.iloc[0]["open"])
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
    if config is None:
        config = StrategyConfig()
    if saved_state is None:
        return None

    features = _prepare_features(df, config, extra_data=extra_data)
    engine_config = _build_engine_config(config)
    engine = EchoTrendV3Engine(engine_config)
    state = engine.import_state(saved_state)

    watermark = pd.Timestamp(saved_state["watermark"])
    if watermark.tzinfo is None:
        watermark = watermark.tz_localize("UTC")

    # Reconstruct prev_row from the watermark bar so the first new bar
    # isn't skipped (engine.on_bar skips when prev_row is None)
    wm_candidates = features.loc[features.index <= watermark]
    if not wm_candidates.empty:
        engine.prev_row = wm_candidates.iloc[-1]

    new_bars = features.loc[features.index > watermark]
    if new_bars.empty:
        return None

    for i in range(len(new_bars)):
        engine.on_bar(state, new_bars.index[i], new_bars.iloc[i])

    new_watermark = features.index[-1]
    price = float(features.iloc[-1]["close"])
    equity = state.equity(price)
    position_value = state.shares * price
    exposure = position_value / equity if equity > 0 else 0.0
    snapshot = engine.export_state(state, new_watermark)

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
        target_exposure=engine.last_target_exposure,
        current_exposure=exposure,
        exposure_state=exposure_state(exposure, engine.last_target_exposure),
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
