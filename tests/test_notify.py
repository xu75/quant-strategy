"""Tests for telegram_notify.py — exposure-aware notifications + webhook verification.

Covers the V3 continuous-exposure contract (F: V3 executable position instruction):
  - An exposure INCREASE must never be labelled "SELL" (the core regression:
    prev executed 1.83% -> current 24.08%, action field = "sell").
  - Notifications show current model exposure, strategy target, and state.
  - Webhook success is decided by the JSON body (data:true/false), not HTTP 200.
  - Binary strategies keep action-change detection.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}):
    from scripts.telegram_notify import (
        build_notifications,
        format_position_message,
        format_position_webhook_json,
        format_message,
        send_webhook,
        strategy_slug,
        EXPOSURE_NOTIFY_THRESHOLD,
    )


def _v3_current(action="sell", curr_exp=0.2408, target=0.0, regime="bear"):
    """A V3-style latest.json (continuous exposure contract)."""
    return {
        "strategy": {"name": "EchoTrend 240 V3", "code": "echotrend_240_v3", "symbol": "MSTR"},
        "current_signal": {
            "action": action,
            "price": 92.11,
            "regime": regime,
            "mode": "neutral",
            "current_exposure": curr_exp,
            "target_exposure": target,
            "exposure_state": "reducing" if target < curr_exp else "increasing",
            "reason": "v3_reduce_neutral",
        },
    }


class TestExposureRegression:
    """Core bug: exposure rose 1.83% -> 24.08% but action='sell' fired a SELL alert."""

    def test_exposure_increase_is_not_sell(self):
        current = _v3_current(action="sell", curr_exp=0.2408, target=0.0)
        prev = {"current_exposure": 0.0183, "target_exposure": 0.0, "regime": "bear", "action": "hold"}
        notes = build_notifications("echotrend_240_v3", current, prev)
        assert len(notes) == 1
        note = notes[0]
        assert note["kind"] == "position"
        # Direction of the actual position change is UP, not a sell.
        assert note["direction"] == "increase"
        assert note["current_exposure"] == 0.2408

    def test_position_message_says_set_to_24_of_total_portfolio(self):
        note = {
            "kind": "position", "strategy": "EchoTrend 240 V3", "slug": "echotrend-240-v3",
            "symbol": "MSTR", "price": 92.11, "regime": "bear",
            "direction": "increase", "prev_exposure": 0.0183,
            "current_exposure": 0.2408, "target_exposure": 0.0, "exposure_state": "reducing",
        }
        msg = format_position_message(note)
        assert "24%" in msg
        assert "SELL" not in msg.upper()
        # Actionable + unambiguous denominator: set MSTR to 24% of TOTAL portfolio.
        assert "Set MSTR to" in msg
        assert "total portfolio" in msg.lower()

    def test_position_webhook_has_msg_and_no_sell(self):
        note = {
            "kind": "position", "strategy": "EchoTrend 240 V3", "slug": "echotrend-240-v3",
            "symbol": "MSTR", "price": 92.11, "regime": "bear",
            "direction": "increase", "prev_exposure": 0.0183,
            "current_exposure": 0.2408, "target_exposure": 0.0, "exposure_state": "reducing",
        }
        payload = format_position_webhook_json(note)
        assert "msg" in payload  # api.chuckfang.com requires a named content field
        assert payload["event"] == "position_change"
        assert payload["current_exposure"] == 0.2408
        assert payload["target_exposure"] == 0.0
        assert "SELL" not in payload["msg"].upper()


class TestExposureDetection:
    def test_no_notification_when_exposure_stable(self):
        current = _v3_current(action="hold", curr_exp=0.24, target=0.0)
        prev = {"current_exposure": 0.239, "target_exposure": 0.0, "regime": "bear", "action": "hold"}
        notes = build_notifications("echotrend_240_v3", current, prev)
        assert notes == []

    def test_regime_flip_triggers_position_note(self):
        current = _v3_current(action="buy", curr_exp=0.90, target=1.0, regime="bull")
        prev = {"current_exposure": 0.88, "target_exposure": 0.0, "regime": "bear", "action": "hold"}
        notes = build_notifications("echotrend_240_v3", current, prev)
        assert len(notes) == 1
        assert notes[0]["regime"] == "bull"

    def test_decrease_direction(self):
        current = _v3_current(action="sell", curr_exp=0.05, target=0.0)
        prev = {"current_exposure": 0.50, "target_exposure": 0.0, "regime": "bear", "action": "sell"}
        notes = build_notifications("echotrend_240_v3", current, prev)
        assert len(notes) == 1
        assert notes[0]["direction"] == "decrease"

    def test_threshold_constant(self):
        assert 0 < EXPOSURE_NOTIFY_THRESHOLD <= 0.05


class TestActionNotificationBinary:
    """Binary strategies (no current_exposure) keep action-change detection."""

    def test_binary_buy_still_notified(self):
        current = {
            "strategy": {"name": "N100 Guard-Z", "code": "n100_guard_z", "symbol": "QQQ"},
            "current_signal": {"action": "buy", "price": 500.0, "reason": "NDX_INVESTED"},
        }
        prev = {"action": "hold"}
        notes = build_notifications("n100_guard_z", current, prev)
        assert len(notes) == 1
        assert notes[0]["kind"] == "action"
        assert notes[0]["new_action"] == "buy"

    def test_binary_hold_no_notification(self):
        current = {
            "strategy": {"name": "N100 Guard-Z", "code": "n100_guard_z", "symbol": "QQQ"},
            "current_signal": {"action": "hold", "price": 500.0},
        }
        prev = {"action": "hold"}
        notes = build_notifications("n100_guard_z", current, prev)
        assert notes == []


class TestWebhookVerification:
    def _sub(self):
        return {"id": "xu_mate80", "url": "https://api.chuckfang.com/xxx/", "format": "json"}

    def _note(self):
        return {
            "kind": "position", "strategy": "EchoTrend 240 V3", "slug": "echotrend-240-v3",
            "symbol": "MSTR", "price": 92.11, "regime": "bear", "direction": "increase",
            "prev_exposure": 0.0183, "current_exposure": 0.2408, "target_exposure": 0.0,
            "exposure_state": "reducing",
        }

    def _resp(self, body: str, status: int = 200):
        m = MagicMock()
        m.status = status
        m.read.return_value = body.encode()
        m.__enter__ = lambda s: s
        m.__exit__ = lambda s, *a: False
        return m

    def test_data_false_is_failure(self):
        with patch("scripts.telegram_notify.urlopen", return_value=self._resp('{"data":false,"msg":"缺少参数"}')):
            assert send_webhook(self._sub(), self._note()) is False

    def test_data_true_is_success(self):
        with patch("scripts.telegram_notify.urlopen", return_value=self._resp('{"data":true,"msg":"发送成功"}')):
            assert send_webhook(self._sub(), self._note()) is True

    def test_non_json_trusts_status(self):
        with patch("scripts.telegram_notify.urlopen", return_value=self._resp('OK')):
            assert send_webhook(self._sub(), self._note()) is True

    def test_http_error_is_failure(self):
        with patch("scripts.telegram_notify.urlopen", return_value=self._resp('err', status=500)):
            assert send_webhook(self._sub(), self._note()) is False


class TestStrategySlug:
    def test_underscore_to_hyphen(self):
        assert strategy_slug("echotrend_240_v3") == "echotrend-240-v3"
