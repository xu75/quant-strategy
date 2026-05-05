from __future__ import annotations

"""EchoTrend 240 Strategy Engine — v9 trend_regime variant.

Complete state machine ported from mstr-strategy-clowder/src/strategies.py.
Owns all stateful logic: V6 scoring, regime gate, execution, portfolio state.

Architecture:
  Layer 0: V6 base target (three-dimensional scoring → 0.70–1.10)
  Layer T: BTC 4H regime ceiling (bear=0.0, bull=1.10)
  Final:   min(v6_target, regime_ceiling)
  Execution: symmetric (step up/down with cooldown and reentry)
"""

from dataclasses import dataclass, field
from math import sqrt

import pandas as pd


@dataclass
class PortfolioState:
    cash: float
    core_shares: float
    initial_shares: float
    max_shares: float
    initial_capital: float = 0.0
    total_costs: float = 0.0

    @property
    def total_shares(self) -> float:
        return self.core_shares

    def equity(self, price: float) -> float:
        return self.cash + self.total_shares * price


@dataclass
class RebalanceEvent:
    timestamp: pd.Timestamp
    side: str  # "buy" or "sell"
    qty: float
    price: float  # fill price (after slippage)
    reason: str
    target_exposure: float
    current_exposure: float
    mode: str
    scores: dict = field(default_factory=dict)


class EchoTrendEngine:
    """v9 trend_regime state machine.

    Ported from VolTargetDdCapV9Strategy(variant="trend_regime").
    """

    def __init__(self, config: dict):
        self.v6 = config.get("v6", {})
        self.v9 = config.get("v9", {})
        self.costs = config.get("costs", {
            "commission_rate": 0.0002,
            "slippage_rate": 0.0003,
        })
        self.rsi_window = config.get("rsi_window", 14)

        # V6 state
        self.bar_index = 0
        self.last_rebalance_bar = -10_000
        self.exposure_ema: float | None = None
        self.mode = "neutral"
        self.mode_counter = 0
        self.last_scores: dict[str, float] = {}

        # Regime gate state
        tr = self.v9.get("trend_regime", {})
        self.trend_regime = "bull"
        self.regime_counter = 0
        self.regime_change_bar = -10_000
        self.regime_flips = 0

        # Execution records
        self.rebalances: list[RebalanceEvent] = []
        self._pending_order: dict | None = None

    def initial_state(self, first_price: float, initial_capital: float = 100_000) -> PortfolioState:
        initial_shares = initial_capital / first_price
        return PortfolioState(
            cash=0.0,
            core_shares=initial_shares,
            initial_shares=initial_shares,
            max_shares=initial_shares * self.v6.get("max_exposure", 1.10),
            initial_capital=initial_capital,
        )

    # ---------------------------------------------------------------
    # State serialization (for daily-signal incremental mode)
    # ---------------------------------------------------------------
    def export_state(self, state: PortfolioState, watermark: pd.Timestamp) -> dict:
        """Export all path-dependent state for incremental resume."""
        return {
            "engine": {
                "bar_index": self.bar_index,
                "exposure_ema": self.exposure_ema,
                "last_scores": dict(self.last_scores),
                "mode": self.mode,
                "mode_counter": self.mode_counter,
                "trend_regime": self.trend_regime,
                "regime_counter": self.regime_counter,
                "regime_change_bar": self.regime_change_bar,
                "regime_flips": self.regime_flips,
                "last_rebalance_bar": self.last_rebalance_bar,
                "pending_order": dict(self._pending_order) if self._pending_order else None,
            },
            "portfolio": {
                "cash": state.cash,
                "core_shares": state.core_shares,
                "initial_shares": state.initial_shares,
                "initial_capital": state.initial_capital,
                "total_costs": state.total_costs,
            },
            "watermark": watermark.isoformat(),
        }

    def import_state(self, snapshot: dict) -> PortfolioState:
        """Restore engine + portfolio from a previously exported snapshot.

        Returns the reconstructed PortfolioState. Engine mutable fields
        are restored in-place.
        """
        eng = snapshot["engine"]
        self.bar_index = eng["bar_index"]
        self.exposure_ema = eng["exposure_ema"]
        self.last_scores = eng.get("last_scores", {})
        self.mode = eng["mode"]
        self.mode_counter = eng["mode_counter"]
        self.trend_regime = eng["trend_regime"]
        self.regime_counter = eng["regime_counter"]
        self.regime_change_bar = eng["regime_change_bar"]
        self.regime_flips = eng["regime_flips"]
        self.last_rebalance_bar = eng["last_rebalance_bar"]
        self._pending_order = eng.get("pending_order")

        port = snapshot["portfolio"]
        return PortfolioState(
            cash=port["cash"],
            core_shares=port["core_shares"],
            initial_shares=port["initial_shares"],
            max_shares=port["initial_shares"] * self.v6.get("max_exposure", 1.10),
            initial_capital=port["initial_capital"],
            total_costs=port["total_costs"],
        )

    # ---------------------------------------------------------------
    # Main bar loop
    # ---------------------------------------------------------------
    def on_bar(
        self, state: PortfolioState, dt: pd.Timestamp, row: pd.Series,
    ) -> None:
        self.bar_index += 1
        price = float(row["close"])

        # Execute pending order from previous bar at current bar's open
        if self._pending_order is not None:
            self._fill_pending(state, dt, row)

        # Layer 0: V6 base target
        base_target = self._target_exposure_v6(row)

        # Layer T: Trend regime ceiling
        regime_ceiling = self._trend_regime_ceiling(row)
        final_target = min(base_target, regime_ceiling)

        # Clamp to absolute bounds
        floor = self.v9.get("min_exposure", 0.0)
        cap = self.v6.get("max_exposure", 1.10)
        final_target = max(floor, min(cap, final_target))

        # Execution
        current_exposure = state.total_shares / state.initial_shares
        gap = final_target - current_exposure

        if abs(gap) < self.v6.get("min_trade_exposure", 0.035):
            return

        self._execute_symmetric(state, dt, price, row, current_exposure, final_target, gap)

    def _fill_pending(self, state: PortfolioState, dt: pd.Timestamp, row: pd.Series) -> None:
        order = self._pending_order
        self._pending_order = None
        open_price = float(row["open"])

        if order["side"] == "buy":
            fill = open_price * (1 + self.costs.get("slippage_rate", 0.0003))
            qty = order["qty"]
            if qty <= 1e-9:
                return
            commission_rate = self.costs.get("commission_rate", 0.0002)
            margin_limit = state.initial_capital * self.v6.get("max_margin_fraction", 0.10)
            max_spend = state.cash + margin_limit
            cost_per_share = fill * (1 + commission_rate)
            if qty * cost_per_share > max_spend:
                qty = max_spend / cost_per_share if max_spend > 0 else 0.0
            if qty <= 1e-9:
                return
            gross = qty * fill
            commission = gross * commission_rate
            state.cash -= gross + commission
            state.core_shares += qty
            state.total_costs += commission + qty * open_price * self.costs.get("slippage_rate", 0.0003)
        else:
            fill = open_price * (1 - self.costs.get("slippage_rate", 0.0003))
            qty = min(order["qty"], state.core_shares)
            if qty <= 1e-9:
                return
            gross = qty * fill
            commission = gross * self.costs.get("commission_rate", 0.0002)
            state.cash += gross - commission
            state.core_shares -= qty
            state.total_costs += commission + qty * open_price * self.costs.get("slippage_rate", 0.0003)

        self.rebalances.append(RebalanceEvent(
            timestamp=dt,
            side=order["side"],
            qty=qty,
            price=fill,
            reason=order["reason"],
            target_exposure=order.get("target_exposure", 0),
            current_exposure=order.get("current_exposure", 0),
            mode=self.mode,
            scores=dict(self.last_scores),
        ))

    # ---------------------------------------------------------------
    # V6 Scoring (source: strategies.py:212-326)
    # ---------------------------------------------------------------
    def _target_exposure_v6(self, row: pd.Series) -> float:
        trend = self._trend_score(row)
        risk = self._risk_score(row)
        rs = self._relative_strength_score(row)
        raw = self._score_to_exposure(trend, risk, rs)
        raw += self._intraday_offset(row, trend, risk)
        mode = self._desired_mode(raw, risk)
        self._update_mode(mode)
        raw = self._mode_floor_ceiling(raw)

        alpha = self.v6.get("target_ema_alpha", 0.30)
        if self.exposure_ema is None:
            self.exposure_ema = raw
        else:
            self.exposure_ema = self.exposure_ema * (1 - alpha) + raw * alpha
        target = max(
            self.v6.get("min_exposure", 0.70),
            min(self.v6.get("max_exposure", 1.10), self.exposure_ema),
        )
        self.last_scores = {"trend": trend, "risk": risk, "rs": rs, "raw": raw, "target": target}
        return target

    def _trend_score(self, row: pd.Series) -> float:
        s = 0.0
        s += 15 if row.get("mstr_1h_return", 0.0) > self.v6.get("mstr_1h_pos", 0.010) else 0
        s += 10 if row.get("mstr_4h_return", 0.0) > self.v6.get("mstr_4h_pos", 0.020) else 0
        s += 15 if row.get("mstr_daily_5d_return", 0.0) > self.v6.get("mstr_5d_pos", 0.030) else 0
        s += 15 if row.get("mstr_daily_20d_return", 0.0) > self.v6.get("mstr_20d_pos", 0.050) else 0
        s += 10 if row.get("mstr_daily_above_ema20", 0.0) >= 1 else 0
        s += 10 if row.get("mstr_daily_above_ema50", 0.0) >= 1 else 0
        s += 10 if row.get("btc_daily_20d_return", 0.0) > self.v6.get("btc_20d_pos", 0.030) else 0
        s += 10 if row.get("market_daily_above_ema20", 0.0) >= 1 else 0
        s += 5 if row.get("market_1h_return", 0.0) > self.v6.get("market_1h_pos", 0.0) else 0
        return min(100.0, s)

    def _risk_score(self, row: pd.Series) -> float:
        s = 0.0
        s += 15 if row.get("mstr_1h_return", 0.0) < self.v6.get("mstr_1h_risk", -0.035) else 0
        s += 15 if row.get("mstr_4h_return", 0.0) < self.v6.get("mstr_4h_risk", -0.060) else 0
        s += 15 if row.get("mstr_daily_5d_return", 0.0) < self.v6.get("mstr_5d_risk", -0.100) else 0
        s += 10 if row.get("btc_1h_return", 0.0) < self.v6.get("btc_1h_risk", -0.020) else 0
        s += 10 if row.get("btc_daily_5d_return", 0.0) < self.v6.get("btc_5d_risk", -0.060) else 0
        s += 10 if row.get("market_1h_return", 0.0) < self.v6.get("market_1h_risk", -0.008) else 0
        s += 10 if row.get("market_daily_5d_return", 0.0) < self.v6.get("market_5d_risk", -0.030) else 0
        s += 10 if row.get("mstr_btc_rs_4h", 0.0) < self.v6.get("rs_4h_risk", -0.040) else 0
        try:
            day_range_check = row.get("day_range", 0.0) > self.v6.get("day_range_risk", 0.090)
            vwap_check = float(row.get("close", 0)) < float(row.get("vwap", float("inf")))
            s += 5 if day_range_check and vwap_check else 0
        except (TypeError, ValueError):
            pass
        return min(100.0, s)

    def _relative_strength_score(self, row: pd.Series) -> float:
        s = 50.0
        s += 15 if row.get("mstr_btc_rs_1h", 0.0) > self.v6.get("rs_1h_pos", 0.010) else 0
        s += 15 if row.get("mstr_btc_rs_4h", 0.0) > self.v6.get("rs_4h_pos", 0.020) else 0
        s += 10 if row.get("mstr_btc_ratio_20", 0.0) > self.v6.get("ratio_20_pos", 0.030) else 0
        s -= 15 if row.get("mstr_btc_rs_1h", 0.0) < self.v6.get("rs_1h_neg", -0.020) else 0
        s -= 15 if row.get("mstr_btc_rs_4h", 0.0) < self.v6.get("rs_4h_neg", -0.040) else 0
        s -= 10 if row.get("mstr_btc_ratio_20", 0.0) < self.v6.get("ratio_20_neg", -0.050) else 0
        return max(0.0, min(100.0, s))

    def _score_to_exposure(self, trend: float, risk: float, rs: float) -> float:
        base = self.v6.get("base_exposure", 1.00)
        return (base
                + (trend - 50) / 50 * self.v6.get("trend_weight", 0.12)
                - risk / 100 * self.v6.get("risk_weight", 0.40)
                + (rs - 50) / 50 * self.v6.get("relative_strength_weight", 0.07))

    def _intraday_offset(self, row: pd.Series, trend: float, risk: float) -> float:
        offset = 0.0
        if trend >= self.v6.get("dip_min_trend_score", 55) and risk < self.v6.get("dip_max_risk_score", 45):
            try:
                close_val = float(row.get("close", 0))
                vwap_val = float(row.get("vwap", 0))
                if vwap_val > 0 and close_val < vwap_val * (1 - self.v6.get("dip_vwap_dev", 0.010)):
                    if row.get("btc_1h_return", 0.0) > -0.010:
                        offset += self.v6.get("dip_add", 0.03)
            except (TypeError, ValueError):
                pass
        if risk < 35 and trend >= 70:
            if row.get("mstr_1h_return", 0.0) > 0.030 and row.get("btc_1h_return", 0.0) > 0:
                offset += self.v6.get("breakout_add", 0.03)
        if risk < self.v6.get("overheat_max_risk_score", 50):
            if row.get("rsi", 50.0) > self.v6.get("overheat_rsi", 82):
                if row.get("vwap_deviation", 0.0) > self.v6.get("overheat_vwap_dev", 0.040):
                    offset -= self.v6.get("overheat_reduce", 0.03)
        cap = self.v6.get("max_intraday_offset", 0.05)
        return max(-cap, min(cap, offset))

    def _desired_mode(self, raw: float, risk: float) -> str:
        if risk >= self.v6.get("crash_risk_score", 70):
            return "crash"
        if risk >= self.v6.get("risk_off_score", 45):
            return "risk_off"
        if raw >= self.v6.get("bull_exposure_threshold", 1.01):
            return "bull"
        return "neutral"

    def _update_mode(self, desired: str) -> None:
        if desired == self.mode:
            self.mode_counter = 0
            return
        self.mode_counter += 1
        required = self.v6.get("mode_confirm_bars", 6)
        if desired in {"risk_off", "crash"}:
            required = self.v6.get("risk_mode_confirm_bars", 3)
        if desired == "bull":
            required = self.v6.get("bull_mode_confirm_bars", 8)
        if self.mode_counter >= required:
            self.mode = desired
            self.mode_counter = 0

    def _mode_floor_ceiling(self, raw: float) -> float:
        floors = self.v6.get("mode_floors", {})
        ceilings = self.v6.get("mode_ceilings", {})
        raw = max(floors.get(self.mode, self.v6.get("min_exposure", 0.70)), raw)
        raw = min(ceilings.get(self.mode, self.v6.get("max_exposure", 1.10)), raw)
        return raw

    # ---------------------------------------------------------------
    # Regime gate (source: strategies.py:596-643)
    # ---------------------------------------------------------------
    def _trend_regime_ceiling(self, row: pd.Series) -> float:
        tr = self.v9.get("trend_regime", {})
        freeze_bars = tr.get("freeze_bars", 0)

        if freeze_bars > 0 and (self.bar_index - self.regime_change_bar) < freeze_bars:
            if self.trend_regime == "bear":
                return tr.get("bear_ceiling", 0.0)
            return tr.get("bull_ceiling", 1.10)

        ma_field = tr.get("ma_field", "btc_4h_above_sma240")
        if ma_field not in row.index:
            raise KeyError(f"Regime field '{ma_field}' missing from feature row — data pipeline incomplete")
        above_ma = float(row[ma_field])
        desired = "bull" if above_ma >= 1.0 else "bear"

        if desired != self.trend_regime:
            self.regime_counter += 1
            if desired == "bear":
                required = tr.get("bear_confirm_bars", 3)
            else:
                required = tr.get("bull_confirm_bars", 3)
            if self.regime_counter >= required:
                self.trend_regime = desired
                self.regime_counter = 0
                self.regime_change_bar = self.bar_index
                self.regime_flips += 1
        else:
            self.regime_counter = 0

        if self.trend_regime == "bear":
            return tr.get("bear_ceiling", 0.0)
        return tr.get("bull_ceiling", 1.10)

    # ---------------------------------------------------------------
    # Reentry logic (source: strategies.py:317-326)
    # ---------------------------------------------------------------
    def _v6_reentry(self, row: pd.Series, current_exp: float, target_exp: float) -> bool:
        if target_exp <= current_exp:
            return False
        votes = 0
        votes += row.get("mstr_1h_return", 0.0) >= self.v6.get("reentry_mstr_1h", 0.035)
        votes += row.get("btc_1h_return", 0.0) >= self.v6.get("reentry_btc_1h", 0.010)
        votes += row.get("market_1h_return", 0.0) >= self.v6.get("reentry_market_1h", 0.003)
        votes += row.get("mstr_btc_rs_1h", 0.0) >= self.v6.get("reentry_rs_1h", 0.010)
        try:
            votes += float(row.get("close", 0)) >= float(row.get("vwap", float("inf")))
        except (TypeError, ValueError):
            pass
        return votes >= self.v6.get("reentry_min_votes", 3)

    # ---------------------------------------------------------------
    # Symmetric execution (source: strategies.py:645-670)
    # ---------------------------------------------------------------
    def _execute_symmetric(
        self, state: PortfolioState, dt: pd.Timestamp, price: float,
        row: pd.Series, current_exp: float, target_exp: float, gap: float,
    ) -> None:
        gap_shares = gap * state.initial_shares
        if gap_shares > 0:
            strong_reentry = self._v6_reentry(row, current_exp, target_exp)
            cooldown = self.v6.get("cooldown_bars", 39)
            if not strong_reentry and self.bar_index - self.last_rebalance_bar < cooldown:
                return
            step = self.v6.get("fast_reentry_step", 0.90) if strong_reentry else self.v6.get("max_step_up", 0.55)
            self._queue_buy(state, gap_shares * step, f"v9_trend_raise_{self.mode}", target_exp, current_exp)
            if self._pending_order is not None:
                self.last_rebalance_bar = self.bar_index
        else:
            step = self.v6.get("crash_step_down", 0.80) if self.mode == "crash" else self.v6.get("max_step_down", 0.50)
            self._queue_sell(state, abs(gap_shares) * step, f"v9_trend_reduce_{self.mode}", target_exp, current_exp)
            if self._pending_order is not None:
                self.last_rebalance_bar = self.bar_index

    def _queue_buy(self, state: PortfolioState, qty: float, reason: str,
                   target_exp: float, current_exp: float) -> None:
        max_allowed = max(state.max_shares - state.total_shares, 0)
        qty = min(qty, max_allowed)
        if qty <= 1e-9:
            return
        self._pending_order = {
            "side": "buy", "qty": qty, "reason": reason,
            "target_exposure": target_exp, "current_exposure": current_exp,
        }

    def _queue_sell(self, state: PortfolioState, qty: float, reason: str,
                    target_exp: float, current_exp: float) -> None:
        qty = min(qty, state.core_shares)
        if qty <= 1e-9:
            return
        self._pending_order = {
            "side": "sell", "qty": qty, "reason": reason,
            "target_exposure": target_exp, "current_exposure": current_exp,
        }

    # ---------------------------------------------------------------
    # Backtest runner
    # ---------------------------------------------------------------
    def run_backtest(
        self,
        features: pd.DataFrame,
        initial_capital: float = 100_000,
    ) -> "EngineBacktestResult":
        """Run the full strategy over a feature DataFrame.

        features must be indexed by timestamp and contain all required columns
        (from indicators.add_features + add_daily_trend_features + add_btc_4h_trend_features).
        """
        if features.empty:
            raise ValueError("Empty features DataFrame")

        first_price = float(features.iloc[0]["close"])
        state = self.initial_state(first_price, initial_capital)

        equity_points: list[dict] = []
        exposure_log: list[dict] = []

        for i in range(len(features)):
            row = features.iloc[i]
            dt = features.index[i]
            self.on_bar(state, dt, row)

            price = float(row["close"])
            eq = state.equity(price)
            exp = state.total_shares / state.initial_shares
            equity_points.append({"timestamp": dt, "equity": eq})
            exposure_log.append({
                "timestamp": dt, "exposure": exp,
                "mode": self.mode, "regime": self.trend_regime,
                **self.last_scores,
            })

        equity_df = pd.DataFrame(equity_points)
        exposure_df = pd.DataFrame(exposure_log)

        final_equity = float(equity_df.iloc[-1]["equity"])
        total_return = (final_equity - initial_capital) / initial_capital * 100
        max_dd = _max_drawdown(equity_df["equity"])
        sharpe = _annualized_sharpe(equity_df)

        first_price_val = float(features.iloc[0]["close"])
        last_price_val = float(features.iloc[-1]["close"])
        bh_return = (last_price_val - first_price_val) / first_price_val * 100
        bh_dd = _max_drawdown(features["close"].astype(float))

        return EngineBacktestResult(
            equity_curve=equity_df,
            exposure_log=exposure_df,
            rebalances=list(self.rebalances),
            total_return_pct=total_return,
            max_drawdown_pct=max_dd * 100,
            sharpe_ratio=sharpe,
            total_rebalances=len(self.rebalances),
            total_costs=state.total_costs,
            buy_hold_return_pct=bh_return,
            buy_hold_max_drawdown_pct=bh_dd * 100,
            start_date=features.index[0],
            end_date=features.index[-1],
            regime_flips=self.regime_flips,
            final_exposure=state.total_shares / state.initial_shares,
            final_mode=self.mode,
            final_regime=self.trend_regime,
        )


@dataclass
class EngineBacktestResult:
    equity_curve: pd.DataFrame
    exposure_log: pd.DataFrame
    rebalances: list[RebalanceEvent]
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    total_rebalances: int
    total_costs: float
    buy_hold_return_pct: float
    buy_hold_max_drawdown_pct: float
    start_date: pd.Timestamp
    end_date: pd.Timestamp
    regime_flips: int
    final_exposure: float
    final_mode: str
    final_regime: str


def _max_drawdown(values) -> float:
    series = [float(v) for v in values if pd.notna(v)]
    if not series:
        return 0.0
    peak = series[0]
    max_dd = 0.0
    for value in series:
        if value > peak:
            peak = value
        if peak <= 0:
            continue
        dd = (peak - value) / peak
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _annualized_sharpe(equity_df: pd.DataFrame) -> float:
    if len(equity_df) < 3:
        return 0.0
    returns = equity_df["equity"].astype(float).pct_change().dropna()
    if len(returns) < 2:
        return 0.0
    avg_ret = float(returns.mean())
    std_ret = float(returns.std(ddof=1))
    if std_ret <= 0:
        return 0.0
    ts = pd.to_datetime(equity_df["timestamp"])
    span_seconds = (ts.iloc[-1] - ts.iloc[0]).total_seconds()
    if span_seconds <= 0:
        return 0.0
    elapsed_years = span_seconds / (365.25 * 24 * 3600)
    bars_yr = (len(ts) - 1) / elapsed_years
    return avg_ret / std_ret * sqrt(bars_yr)
