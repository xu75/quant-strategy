"""Tests for telegram_notify.py — exposure-aware notifications + webhook verification.

Covers the V3 continuous-exposure contract (F: V3 executable position instruction):
  - An exposure INCREASE must never be labelled "SELL" (the core regression:
    prev executed 1.83% -> current 24.08%, action field = "sell").
  - Notifications show current model exposure, strategy target, and state.
  - Webhook success is decided by the JSON body (data:true/false), not HTTP 200.
  - Binary strategies keep action-change detection.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}):
    from scripts.telegram_notify import (
        build_notifications,
        format_position_message,
        format_position_webhook_json,
        format_message,
        format_webhook_json,
        get_previous_signal_data,
        _telegram_message,
        send_webhook,
        strategy_slug,
        load_subscribers,
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


def _trendlock_current(*, action="hold", in_position=True, timestamp="2026-07-10T08:00:00+00:00"):
    """A binary TrendLock snapshot where action may have returned to hold."""
    return {
        "strategy": {
            "name": "TrendLock 40 Plus",
            "code": "btc_ma_trend_plus",
            "symbol": "BTC-USDT",
        },
        "current_signal": {
            "action": action,
            "price": 64_407.0,
            "timestamp": timestamp,
            "reason": "Holding, price above MA240",
        },
        "position": {"in_position": in_position, "entry_bar_idx": 14291 if in_position else None},
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


class TestBinaryPositionTransitionDetection:
    """Regression coverage for the TrendLock signals missed in July 2026."""

    def test_entry_detected_after_action_has_returned_to_hold(self):
        current = _trendlock_current(action="hold", in_position=True)
        prev = {"action": "hold", "in_position": False}

        notes = build_notifications("btc_ma_trend_plus", current, prev)

        assert len(notes) == 1
        assert notes[0]["kind"] == "action"
        assert notes[0]["new_action"] == "buy"
        assert notes[0]["detection_source"] == "position_transition"
        assert notes[0]["old_in_position"] is False
        assert notes[0]["new_in_position"] is True

    def test_exit_detected_after_action_has_returned_to_hold(self):
        current = _trendlock_current(
            action="hold",
            in_position=False,
            timestamp="2026-07-14T08:00:00+00:00",
        )
        prev = {"action": "hold", "in_position": True}

        notes = build_notifications("btc_ma_trend_plus", current, prev)

        assert len(notes) == 1
        assert notes[0]["new_action"] == "sell"
        assert notes[0]["detection_source"] == "position_transition"
        assert notes[0]["old_in_position"] is True
        assert notes[0]["new_in_position"] is False

    def test_position_transition_does_not_duplicate_current_action(self):
        current = _trendlock_current(action="sell", in_position=False)
        prev = {"action": "hold", "in_position": True}

        notes = build_notifications("btc_ma_trend_plus", current, prev)

        assert len(notes) == 1
        assert notes[0]["new_action"] == "sell"

    def test_inferred_transition_labels_price_as_latest_snapshot(self):
        current = _trendlock_current(action="hold", in_position=True)
        note = build_notifications(
            "btc_ma_trend_plus", current, {"action": "hold", "in_position": False}
        )[0]

        telegram = _telegram_message(note)
        webhook = format_webhook_json(note)

        assert "Latest price:" in telegram
        assert "since the previous pipeline run" in telegram
        assert "latest $" in webhook["msg"]
        assert webhook["price_semantics"] == "latest_snapshot"

    def test_stable_position_and_hold_stays_silent(self):
        current = _trendlock_current(action="hold", in_position=True)
        prev = {"action": "hold", "in_position": True}

        assert build_notifications("btc_ma_trend_plus", current, prev) == []

    def test_stable_position_does_not_fall_back_to_transient_action(self):
        current = _trendlock_current(action="buy", in_position=True)
        prev = {"action": "hold", "in_position": True}

        assert build_notifications("btc_ma_trend_plus", current, prev) == []

    def test_continuous_strategy_keeps_single_exposure_notification(self):
        current = _v3_current(action="sell", curr_exp=0.2408, target=0.0)
        current["position"] = {"in_position": True}
        prev = {
            "action": "hold",
            "regime": "bear",
            "current_exposure": 0.0183,
            "target_exposure": 0.0,
            "in_position": False,
        }

        notes = build_notifications("echotrend_240_v3", current, prev)

        assert len(notes) == 1
        assert notes[0]["kind"] == "position"


class TestPreviousSignalData:
    def test_preserves_false_position_state_from_git_snapshot(self):
        snapshot = {
            "current_signal": {"action": "hold"},
            "position": {"in_position": False},
        }
        completed = MagicMock(returncode=0, stdout=json.dumps(snapshot))

        with patch("scripts.telegram_notify.subprocess.run", return_value=completed):
            prev = get_previous_signal_data("btc_ma_trend_plus")

        assert prev["action"] == "hold"
        assert prev["in_position"] is False


class TestWebhookVerification:
    def _sub(self):
        return {"id": "test_webhook", "url": "https://webhook.example.com/test/", "format": "json"}

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


class TestLoadSubscribers:
    """Test load_subscribers() schema validation and fallback behavior."""

    def test_valid_secret_returns_subscribers(self):
        valid_json = '[{"id":"test","url":"https://example.com/hook/","format":"json"}]'
        with patch.dict(os.environ, {"WEBHOOK_SUBSCRIBERS_JSON": valid_json}):
            subs = load_subscribers()
            assert len(subs) == 1
            assert subs[0]["id"] == "test"
            assert subs[0]["url"] == "https://example.com/hook/"

    def test_invalid_json_exits_non_zero(self):
        with patch.dict(os.environ, {"WEBHOOK_SUBSCRIBERS_JSON": "not json"}):
            with pytest.raises(SystemExit) as exc_info:
                load_subscribers()
            assert exc_info.value.code == 1

    def test_non_array_json_exits_non_zero(self):
        with patch.dict(os.environ, {"WEBHOOK_SUBSCRIBERS_JSON": '{"not":"array"}'}):
            with pytest.raises(SystemExit) as exc_info:
                load_subscribers()
            assert exc_info.value.code == 1

    def test_array_with_non_object_element_exits_non_zero(self):
        with patch.dict(os.environ, {"WEBHOOK_SUBSCRIBERS_JSON": '["string"]'}):
            with pytest.raises(SystemExit) as exc_info:
                load_subscribers()
            assert exc_info.value.code == 1

    def test_missing_required_field_exits_non_zero(self):
        # Missing 'url'
        with patch.dict(os.environ, {"WEBHOOK_SUBSCRIBERS_JSON": '[{"format":"json"}]'}):
            with pytest.raises(SystemExit) as exc_info:
                load_subscribers()
            assert exc_info.value.code == 1

    def test_missing_format_field_exits_non_zero(self):
        # Missing 'format'
        with patch.dict(os.environ, {"WEBHOOK_SUBSCRIBERS_JSON": '[{"url":"https://example.com/"}]'}):
            with pytest.raises(SystemExit) as exc_info:
                load_subscribers()
            assert exc_info.value.code == 1

    def test_url_must_be_string(self):
        # url is a number
        with patch.dict(os.environ, {"WEBHOOK_SUBSCRIBERS_JSON": '[{"url":123,"format":"json"}]'}):
            with pytest.raises(SystemExit) as exc_info:
                load_subscribers()
            assert exc_info.value.code == 1

    def test_url_must_be_http_or_https(self):
        # url doesn't start with http:// or https://
        with patch.dict(os.environ, {"WEBHOOK_SUBSCRIBERS_JSON": '[{"url":"ftp://example.com","format":"json"}]'}):
            with pytest.raises(SystemExit) as exc_info:
                load_subscribers()
            assert exc_info.value.code == 1

    def test_format_must_be_valid_enum(self):
        # format is not in the valid set
        with patch.dict(os.environ, {"WEBHOOK_SUBSCRIBERS_JSON": '[{"url":"https://example.com","format":"jsno"}]'}):
            with pytest.raises(SystemExit) as exc_info:
                load_subscribers()
            assert exc_info.value.code == 1

    def test_url_without_hostname_is_rejected(self):
        # url is just "https://" without hostname
        with patch.dict(os.environ, {"WEBHOOK_SUBSCRIBERS_JSON": '[{"url":"https://","format":"json"}]'}):
            with pytest.raises(SystemExit) as exc_info:
                load_subscribers()
            assert exc_info.value.code == 1

    def test_format_must_be_string_type(self):
        # format is a list instead of string
        with patch.dict(os.environ, {"WEBHOOK_SUBSCRIBERS_JSON": '[{"url":"https://example.com","format":[]}]'}):
            with pytest.raises(SystemExit) as exc_info:
                load_subscribers()
            assert exc_info.value.code == 1

    def test_url_with_userinfo_but_no_hostname_is_rejected(self):
        # url like "https://user@" has no hostname
        with patch.dict(os.environ, {"WEBHOOK_SUBSCRIBERS_JSON": '[{"url":"https://user@","format":"json"}]'}):
            with pytest.raises(SystemExit) as exc_info:
                load_subscribers()
            assert exc_info.value.code == 1

    def test_url_with_query_but_no_hostname_is_rejected(self):
        # url like "https://?x=1" has no hostname
        with patch.dict(os.environ, {"WEBHOOK_SUBSCRIBERS_JSON": '[{"url":"https://?x=1","format":"json"}]'}):
            with pytest.raises(SystemExit) as exc_info:
                load_subscribers()
            assert exc_info.value.code == 1

    def test_no_secret_falls_back_to_local_file(self):
        with patch.dict(os.environ, {"WEBHOOK_SUBSCRIBERS_JSON": ""}):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("builtins.open", MagicMock(return_value=MagicMock(__enter__=lambda s: MagicMock(read=lambda: '[]')))):
                    subs = load_subscribers()
                    assert subs == []

    def test_no_secret_and_no_file_returns_empty(self):
        with patch.dict(os.environ, {"WEBHOOK_SUBSCRIBERS_JSON": ""}):
            with patch("pathlib.Path.exists", return_value=False):
                subs = load_subscribers()
                assert subs == []
