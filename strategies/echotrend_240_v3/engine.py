from __future__ import annotations

"""EchoTrend 240 V3 Strategy Engine — decision_row execution model.

Key changes from V2:
- Decision-row execution: fill at current bar's open (no pending order delay)
- Current equity sizing (not fixed initial_shares)
- Asymmetric confirm: bear=3, bull=9 (MSTR 1H bars)
- Circuit breaker: RTH intraday_high→bar_low -10%, reset next day
- Crash mode: 1-bar confirm, step_down=0.80
- Strong reentry: 3/5 votes → step_up=0.90
- Cooldown: 0 (removed)
"""

from dataclasses import dataclass, field
from math import sqrt

import pandas as pd


@dataclass
class PortfolioState:
    cash: float
    shares: float
    initial_capital: float = 0.0
    total_costs: float = 0.0

    def equity(self, price: float) -> float:
        return self.cash + self.shares * price


@dataclass
class RebalanceEvent:
    timestamp: pd.Timestamp
    side: str
    qty: float
    price: float
    reason: str
    target_exposure: float
    current_exposure: float
    mode: str
    scores: dict = field(default_factory=dict)


class EchoTrendV3Engine:
    """V3 engine: decision_row execution + CB + crash confirm."""

    def __init__(self, config: dict):
        self.v6 = config.get("v6", {})
        self.v9 = config.get("v9", {})
        self.costs = config.get("costs", {
            "commission_rate": 0.0005,
            "slippage_rate": 0.0005,
        })

        # State
        self.bar_index = 0
        self.last_rebalance_bar = -10_000
        self.exposure_ema: float | None = None
        self.mode = "neutral"
        self.mode_counter = 0
        self.last_scores: dict[str, float] = {}
        # True strategy target at the most recent decision bar (regime ceiling,
        # CB override). This is the STRATEGY target — distinct from executed
        # exposure. Seeded to the bull default until the first bar computes it.
        self.last_target_exposure: float = 1.0

        # Regime gate state
        self.trend_regime = "bull"
        self.regime_counter = 0
        self.regime_change_bar = -10_000
        self.regime_flips = 0

        # Circuit breaker state
        self.cb_triggered = False
        self.cb_trigger_date: str | None = None
        self.prev_bar_high: float = 0.0
        self.prev_bar_low: float = 0.0
        self.intraday_high: float = 0.0
        self.current_date: str | None = None

        # Previous bar tracking (for lookahead-free scoring)
        self.prev_row: pd.Series | None = None

        # Execution records
        self.rebalances: list[RebalanceEvent] = []

    def initial_state(self, first_price: float, initial_capital: float = 100_000) -> PortfolioState:
        shares = initial_capital / first_price
        return PortfolioState(
            cash=0.0,
            shares=shares,
            initial_capital=initial_capital,
        )

    # ---------------------------------------------------------------
    # State serialization
    # ---------------------------------------------------------------
    def export_state(self, state: PortfolioState, watermark: pd.Timestamp) -> dict:
        return {
            "engine": {
                "bar_index": self.bar_index,
                "exposure_ema": self.exposure_ema,
                "last_scores": dict(self.last_scores),
                "last_target_exposure": self.last_target_exposure,
                "mode": self.mode,
                "mode_counter": self.mode_counter,
                "trend_regime": self.trend_regime,
                "regime_counter": self.regime_counter,
                "regime_change_bar": self.regime_change_bar,
                "regime_flips": self.regime_flips,
                "last_rebalance_bar": self.last_rebalance_bar,
                "cb_triggered": self.cb_triggered,
                "cb_trigger_date": self.cb_trigger_date,
                "prev_bar_high": self.prev_bar_high,
                "prev_bar_low": self.prev_bar_low,
                "intraday_high": self.intraday_high,
                "current_date": self.current_date,
            },
            "portfolio": {
                "cash": state.cash,
                "shares": state.shares,
                "initial_capital": state.initial_capital,
                "total_costs": state.total_costs,
            },
            "watermark": watermark.isoformat(),
        }

    def import_state(self, snapshot: dict) -> PortfolioState:
        eng = snapshot["engine"]
        self.bar_index = eng["bar_index"]
        self.exposure_ema = eng["exposure_ema"]
        self.last_scores = eng.get("last_scores", {})
        self.last_target_exposure = eng.get("last_target_exposure", 1.0)
        self.mode = eng["mode"]
        self.mode_counter = eng["mode_counter"]
        self.trend_regime = eng["trend_regime"]
        self.regime_counter = eng["regime_counter"]
        self.regime_change_bar = eng["regime_change_bar"]
        self.regime_flips = eng["regime_flips"]
        self.last_rebalance_bar = eng["last_rebalance_bar"]
        self.cb_triggered = eng.get("cb_triggered", False)
        self.cb_trigger_date = eng.get("cb_trigger_date")
        self.prev_bar_high = eng.get("prev_bar_high", 0.0)
        self.prev_bar_low = eng.get("prev_bar_low", 0.0)
        self.intraday_high = eng.get("intraday_high", 0.0)
        self.current_date = eng.get("current_date")

        port = snapshot["portfolio"]
        return PortfolioState(
            cash=port["cash"],
            shares=port["shares"],
            initial_capital=port["initial_capital"],
            total_costs=port["total_costs"],
        )

    # ---------------------------------------------------------------
    # Main bar loop — decision_row execution
    # Scoring uses decision_row = prev_row + current-open-known overlay.
    # Current-open-known fields (already shift(1) in indicators.py):
    #   btc_4h_above_sma*, mstr_daily_*, btc_daily_*, market_daily_*, open
    # Intraday fields (mstr_1h_return, btc_1h_return, etc.) stay from prev_row.
    # ---------------------------------------------------------------

    CURRENT_OPEN_KNOWN_PREFIXES = (
        "btc_4h_above_sma",
        "btc_4h_sma",
        "mstr_daily_",
        "btc_daily_",
        "market_daily_",
    )

    CURRENT_OPEN_KNOWN_EXACT = frozenset({"open"})

    def _build_decision_row(self, current_row: pd.Series, prev_row: pd.Series) -> pd.Series:
        """Compose decision_row: prev_row base + current-open-known overlay."""
        decision = prev_row.copy()
        for col in current_row.index:
            if col in self.CURRENT_OPEN_KNOWN_EXACT:
                decision[col] = current_row[col]
            elif any(col.startswith(p) for p in self.CURRENT_OPEN_KNOWN_PREFIXES):
                decision[col] = current_row[col]
        return decision

    def on_bar(self, state: PortfolioState, dt: pd.Timestamp, row: pd.Series) -> None:
        self.bar_index += 1
        open_price = float(row["open"])
        high_price = float(row["high"])
        low_price = float(row["low"])

        # Track trading day for CB reset
        bar_date = str(dt.date()) if hasattr(dt, 'date') else str(dt)[:10]
        if bar_date != self.current_date:
            self.current_date = bar_date
            self.intraday_high = 0.0
            if self.cb_triggered and bar_date != self.cb_trigger_date:
                self.cb_triggered = False

        # Circuit breaker: check if previous completed bar had
        # intraday_high→bar_low drop >= threshold. Sets cb_triggered flag
        # which forces target_exposure=0 through normal gap*step execution.
        if not self.cb_triggered and self.prev_bar_high > 0 and self.intraday_high > 0:
            cb_threshold = self.v9.get("circuit_breaker", {}).get("intraday_drop", -0.10)
            effective_high = max(self.intraday_high, self.prev_bar_high)
            drop = (self.prev_bar_low - effective_high) / effective_high
            if drop <= cb_threshold:
                self.cb_triggered = True
                self.cb_trigger_date = bar_date

        # Skip first bar (no prev_row yet for scoring)
        if self.prev_row is None:
            self.intraday_high = max(self.intraday_high, high_price)
            self.prev_bar_high = high_price
            self.prev_bar_low = low_price
            self.prev_row = row
            return

        # Layer T: Regime gate (btc_4h_above_sma is already shift(1)+ffill,
        # safe to read from current row)
        regime_ceiling = self._trend_regime_ceiling(row)

        # Build decision_row: prev_row base + current-open-known overlay
        decision_row = self._build_decision_row(row, self.prev_row)

        # V6 scoring uses decision_row (prev_row intraday + current daily/regime)
        base_target = self._target_exposure_v6(decision_row)
        final_target = min(base_target, regime_ceiling)

        # CB active → target = 0 (per SPEC: cb_active overrides target)
        if self.cb_triggered:
            final_target = 0.0

        # Clamp
        final_target = max(0.0, min(1.0, final_target))

        # Record the TRUE strategy target for this decision bar (regime ceiling
        # / CB override), independent of whether a trade actually executes. This
        # is what the UI/notifier report as "strategy target".
        self.last_target_exposure = final_target

        # Current exposure based on open price (known at decision time)
        equity = state.equity(open_price)
        if equity <= 0:
            self.intraday_high = max(self.intraday_high, high_price)
            self.prev_bar_high = high_price
            self.prev_bar_low = low_price
            self.prev_row = row
            return
        position_value = state.shares * open_price
        current_exposure = position_value / equity
        gap = final_target - current_exposure

        if abs(gap) < self.v6.get("min_trade_exposure", 0.035):
            self.intraday_high = max(self.intraday_high, high_price)
            self.prev_bar_high = high_price
            self.prev_bar_low = low_price
            self.prev_row = row
            return

        self._execute_decision_row(state, dt, open_price, open_price, decision_row, current_exposure, final_target, gap)

        # Update tracking AFTER decision
        self.intraday_high = max(self.intraday_high, high_price)
        self.prev_bar_high = high_price
        self.prev_bar_low = low_price
        self.prev_row = row

    # ---------------------------------------------------------------
    # Regime gate — asymmetric confirm (bear=3, bull=9 on MSTR 1H)
    # ---------------------------------------------------------------
    def _trend_regime_ceiling(self, row: pd.Series) -> float:
        tr = self.v9.get("trend_regime", {})
        ma_field = tr.get("ma_field", "btc_4h_above_sma240")
        if ma_field not in row.index:
            raise KeyError(f"Regime field '{ma_field}' missing from feature row")
        above_ma = float(row[ma_field])
        desired = "bull" if above_ma >= 1.0 else "bear"

        if desired != self.trend_regime:
            self.regime_counter += 1
            if desired == "bear":
                required = tr.get("bear_confirm_bars", 3)
            else:
                required = tr.get("bull_confirm_bars", 9)
            if self.regime_counter >= required:
                self.trend_regime = desired
                self.regime_counter = 0
                self.regime_change_bar = self.bar_index
                self.regime_flips += 1
        else:
            self.regime_counter = 0

        if self.trend_regime == "bear":
            return tr.get("bear_ceiling", 0.0)
        return tr.get("bull_ceiling", 1.0)

    # ---------------------------------------------------------------
    # V6 Scoring
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
            self.v6.get("min_exposure", 1.00),
            min(self.v6.get("max_exposure", 1.00), self.exposure_ema),
        )
        self.last_scores = {"trend": trend, "risk": risk, "rs": rs, "raw": raw, "target": target}
        return target

    def _trend_score(self, row: pd.Series) -> float:
        s = 0.0
        s += 15 if row.get("mstr_1h_return", 0.0) > 0.010 else 0
        s += 10 if row.get("mstr_4h_return", 0.0) > 0.020 else 0
        s += 15 if row.get("mstr_daily_5d_return", 0.0) > 0.030 else 0
        s += 15 if row.get("mstr_daily_20d_return", 0.0) > 0.050 else 0
        s += 10 if row.get("mstr_daily_above_ema20", 0.0) >= 1 else 0
        s += 10 if row.get("mstr_daily_above_ema50", 0.0) >= 1 else 0
        s += 10 if row.get("btc_daily_20d_return", 0.0) > 0.030 else 0
        s += 10 if row.get("market_daily_above_ema20", 0.0) >= 1 else 0
        s += 5 if row.get("market_1h_return", 0.0) > 0.0 else 0
        return min(100.0, s)

    def _risk_score(self, row: pd.Series) -> float:
        s = 0.0
        s += 15 if row.get("mstr_1h_return", 0.0) < -0.035 else 0
        s += 15 if row.get("mstr_4h_return", 0.0) < -0.060 else 0
        s += 15 if row.get("mstr_daily_5d_return", 0.0) < -0.100 else 0
        s += 10 if row.get("btc_1h_return", 0.0) < -0.020 else 0
        s += 10 if row.get("btc_daily_5d_return", 0.0) < -0.060 else 0
        s += 10 if row.get("market_1h_return", 0.0) < -0.008 else 0
        s += 10 if row.get("market_daily_5d_return", 0.0) < -0.030 else 0
        s += 10 if row.get("mstr_btc_rs_4h", 0.0) < -0.040 else 0
        try:
            day_range_check = row.get("day_range", 0.0) > 0.090
            vwap_check = float(row.get("close", 0)) < float(row.get("vwap", float("inf")))
            s += 5 if day_range_check and vwap_check else 0
        except (TypeError, ValueError):
            pass
        return min(100.0, s)

    def _relative_strength_score(self, row: pd.Series) -> float:
        s = 50.0
        s += 15 if row.get("mstr_btc_rs_1h", 0.0) > 0.010 else 0
        s += 15 if row.get("mstr_btc_rs_4h", 0.0) > 0.020 else 0
        s += 10 if row.get("mstr_btc_ratio_20", 0.0) > 0.030 else 0
        s -= 15 if row.get("mstr_btc_rs_1h", 0.0) < -0.020 else 0
        s -= 15 if row.get("mstr_btc_rs_4h", 0.0) < -0.040 else 0
        s -= 10 if row.get("mstr_btc_ratio_20", 0.0) < -0.050 else 0
        return max(0.0, min(100.0, s))

    def _score_to_exposure(self, trend: float, risk: float, rs: float) -> float:
        base = 1.00
        return (base
                + (trend - 50) / 50 * 0.12
                - risk / 100 * 0.40
                + (rs - 50) / 50 * 0.07)

    def _intraday_offset(self, row: pd.Series, trend: float, risk: float) -> float:
        offset = 0.0
        if trend >= 55 and risk < 45:
            try:
                close_val = float(row.get("close", 0))
                vwap_val = float(row.get("vwap", 0))
                if vwap_val > 0 and close_val < vwap_val * 0.990:
                    if row.get("btc_1h_return", 0.0) > -0.010:
                        offset += 0.03
            except (TypeError, ValueError):
                pass
        if risk < 35 and trend >= 70:
            if row.get("mstr_1h_return", 0.0) > 0.030 and row.get("btc_1h_return", 0.0) > 0:
                offset += 0.03
        if risk < 50:
            if row.get("rsi", 50.0) > 82:
                if row.get("vwap_deviation", 0.0) > 0.040:
                    offset -= 0.03
        return max(-0.05, min(0.05, offset))

    def _desired_mode(self, raw: float, risk: float) -> str:
        if risk >= 70:
            return "crash"
        if risk >= 45:
            return "risk_off"
        if raw >= 1.01:
            return "bull"
        return "neutral"

    def _update_mode(self, desired: str) -> None:
        if desired == self.mode:
            self.mode_counter = 0
            return
        self.mode_counter += 1
        if desired in {"risk_off", "crash"}:
            required = self.v6.get("risk_mode_confirm_bars", 1)
        elif desired == "bull":
            required = self.v6.get("bull_mode_confirm_bars", 8)
        else:
            required = self.v6.get("mode_confirm_bars", 6)
        if self.mode_counter >= required:
            self.mode = desired
            self.mode_counter = 0

    def _mode_floor_ceiling(self, raw: float) -> float:
        floors = self.v6.get("mode_floors", {})
        ceilings = self.v6.get("mode_ceilings", {})
        raw = max(floors.get(self.mode, 1.00), raw)
        raw = min(ceilings.get(self.mode, 1.00), raw)
        return raw

    # ---------------------------------------------------------------
    # Strong reentry (3/5 votes → step_up=0.90)
    # ---------------------------------------------------------------
    def _v6_reentry(self, row: pd.Series, current_exp: float, target_exp: float) -> bool:
        if target_exp <= current_exp:
            return False
        votes = 0
        votes += row.get("mstr_1h_return", 0.0) >= 0.035
        votes += row.get("btc_1h_return", 0.0) >= 0.010
        votes += row.get("market_1h_return", 0.0) >= 0.003
        votes += row.get("mstr_btc_rs_1h", 0.0) >= 0.010
        try:
            votes += float(row.get("close", 0)) >= float(row.get("vwap", float("inf")))
        except (TypeError, ValueError):
            pass
        return votes >= 3

    # ---------------------------------------------------------------
    # Decision-row execution (current bar open, equity-based sizing)
    # ---------------------------------------------------------------
    def _execute_decision_row(
        self, state: PortfolioState, dt: pd.Timestamp, open_price: float,
        close_price: float, row: pd.Series, current_exp: float, target_exp: float, gap: float,
    ) -> None:
        equity = state.equity(open_price)
        if equity <= 0:
            return

        if gap > 0:
            strong_reentry = self._v6_reentry(row, current_exp, target_exp)
            step = 0.90 if strong_reentry else 0.55
            target_value = equity * target_exp
            current_value = state.shares * open_price
            buy_value = (target_value - current_value) * step
            if buy_value <= 0:
                return
            fill = open_price * (1 + self.costs.get("slippage_rate", 0.0005))
            commission_rate = self.costs.get("commission_rate", 0.0005)
            cost_per_unit = fill * (1 + commission_rate)
            qty = buy_value / cost_per_unit
            max_affordable = state.cash / cost_per_unit if state.cash > 0 else 0.0
            qty = min(qty, max_affordable)
            if qty <= 1e-9:
                return
            gross = qty * fill
            commission = gross * commission_rate
            state.cash -= gross + commission
            state.shares += qty
            state.total_costs += commission + qty * open_price * self.costs.get("slippage_rate", 0.0005)
            self.rebalances.append(RebalanceEvent(
                timestamp=dt, side="buy", qty=qty, price=fill,
                reason=f"v3_raise_{self.mode}", target_exposure=target_exp,
                current_exposure=current_exp, mode=self.mode, scores=dict(self.last_scores),
            ))
            self.last_rebalance_bar = self.bar_index
        else:
            step = 0.80 if self.mode == "crash" else 0.50
            target_value = equity * target_exp
            current_value = state.shares * open_price
            sell_value = (current_value - target_value) * step
            if sell_value <= 0:
                return
            fill = open_price * (1 - self.costs.get("slippage_rate", 0.0005))
            qty = sell_value / fill
            qty = min(qty, state.shares)
            if qty <= 1e-9:
                return
            gross = qty * fill
            commission = gross * self.costs.get("commission_rate", 0.0005)
            state.cash += gross - commission
            state.shares -= qty
            state.total_costs += commission + qty * open_price * self.costs.get("slippage_rate", 0.0005)
            self.rebalances.append(RebalanceEvent(
                timestamp=dt, side="sell", qty=qty, price=fill,
                reason=f"v3_reduce_{self.mode}", target_exposure=target_exp,
                current_exposure=current_exp, mode=self.mode, scores=dict(self.last_scores),
            ))
            self.last_rebalance_bar = self.bar_index

    # ---------------------------------------------------------------
    # Backtest runner
    # ---------------------------------------------------------------
    def run_backtest(
        self,
        features: pd.DataFrame,
        initial_capital: float = 100_000,
    ) -> "EngineBacktestResult":
        if features.empty:
            raise ValueError("Empty features DataFrame")

        first_price = float(features.iloc[0]["open"])
        state = self.initial_state(first_price, initial_capital)

        equity_points: list[dict] = []
        exposure_log: list[dict] = []

        for i in range(len(features)):
            row = features.iloc[i]
            dt = features.index[i]
            self.on_bar(state, dt, row)

            price = float(row["close"])
            eq = state.equity(price)
            position_value = state.shares * price
            exp = position_value / eq if eq > 0 else 0.0
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

        final_eq = state.equity(float(features.iloc[-1]["close"]))
        position_value = state.shares * float(features.iloc[-1]["close"])
        final_exp = position_value / final_eq if final_eq > 0 else 0.0

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
            final_exposure=final_exp,
            final_target_exposure=self.last_target_exposure,
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
    final_target_exposure: float
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
