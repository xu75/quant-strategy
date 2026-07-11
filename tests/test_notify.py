"""Tests for telegram_notify.py — signal change and ETF rotation detection."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# We need to patch before import since module reads env vars at import time
with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}):
    from scripts.telegram_notify import (
        format_message,
        format_rotation_message,
        format_rotation_webhook_json,
        strategy_slug,
    )


class TestFormatMessage:
    def test_buy_signal(self):
        msg = format_message("N100 Guard-Z", "n100-guard-z", "hold", "buy",
                             {"price": 500.0, "symbol": "QQQ", "reason": "NDX_INVESTED"})
        assert "N100 Guard-Z" in msg
        assert "HOLD → BUY" in msg
        assert "QQQ" in msg
        assert "$500.00" in msg
        assert "n100-guard-z" in msg

    def test_sell_signal(self):
        msg = format_message("N100 Guard-Z", "n100-guard-z", "buy", "sell",
                             {"price": 400.0, "symbol": "QQQ", "reason": "EXIT"})
        assert "BUY → SELL" in msg
        assert "\U0001f534" in msg  # red circle emoji


class TestFormatRotationMessage:
    def test_basic_rotation_with_decision_proof(self):
        msg = format_rotation_message("N100 Guard-Z", "n100-guard-z", "159660", "159659", {
            "old_holding": "纳指ETF汇添富",
            "holding": "纳斯达克100ETF招商",
            "rotation_reason": {
                "old_etf": "159660",
                "new_etf": "159659",
                "old_name": "纳指ETF汇添富",
                "new_name": "纳斯达克100ETF招商",
                "old_zscore": 2.5,
                "new_zscore": 1.2,
                "zscore_diff": 1.3,
                "zscore_threshold": 1.0,
                "old_premium": 0.06,
                "new_premium": 0.03,
                "premium_diff": 0.03,
                "premium_threshold": 0.0016,
                "holding_days": 15,
                "holding_days_threshold": 10,
            },
        })
        assert "ETF Rotation" in msg
        assert "159660 → 159659" in msg
        assert "纳指ETF汇添富" in msg
        assert "纳斯达克100ETF招商" in msg
        assert "2.50 → 1.20" in msg
        assert "差值 1.30 > 阈值 1.0" in msg
        assert "6.0000% → 3.0000%" in msg
        assert "差值 3.0000% > 阈值 0.1600%" in msg
        assert "持有天数: 15 ≥ 10" in msg
        assert "\U0001f504" in msg  # rotation emoji

    def test_rotation_without_rotation_reason(self):
        """Fallback when rotation_reason is not available (older data)."""
        msg = format_rotation_message("Test", "test", "A", "B", {
            "old_holding": "Old Fund",
            "holding": "New Fund",
        })
        assert "Z-Score" not in msg
        assert "Premium" not in msg
        assert "Old Fund → New Fund" in msg


class TestFormatRotationWebhookJson:
    def test_payload_structure(self):
        payload = format_rotation_webhook_json("N100 Guard-Z", "n100-guard-z", "159660", "159659", {
            "old_holding": "纳指ETF汇添富",
            "holding": "纳斯达克100ETF招商",
            "rotation_reason": {
                "old_etf": "159660",
                "new_etf": "159659",
                "old_name": "纳指ETF汇添富",
                "new_name": "纳斯达克100ETF招商",
                "old_zscore": 2.5,
                "new_zscore": 1.2,
                "zscore_diff": 1.3,
                "zscore_threshold": 1.0,
                "old_premium": 0.06,
                "new_premium": 0.03,
                "premium_diff": 0.03,
                "premium_threshold": 0.0016,
                "holding_days": 15,
                "holding_days_threshold": 10,
            },
        })
        assert payload["event"] == "etf_rotation"
        assert payload["strategy"] == "N100 Guard-Z"
        assert payload["old_etf"] == "159660"
        assert payload["new_etf"] == "159659"
        assert payload["old_name"] == "纳指ETF汇添富"
        assert payload["new_name"] == "纳斯达克100ETF招商"
        assert payload["old_zscore"] == 2.5
        assert payload["new_zscore"] == 1.2
        assert payload["zscore_diff"] == 1.3
        assert payload["zscore_threshold"] == 1.0
        assert payload["old_premium"] == 0.06
        assert payload["new_premium"] == 0.03
        assert payload["premium_diff"] == 0.03
        assert payload["premium_threshold"] == 0.0016
        assert payload["holding_days"] == 15
        assert payload["holding_days_threshold"] == 10
        assert "n100-guard-z" in payload["url"]


class TestStrategySlug:
    def test_underscore_to_hyphen(self):
        assert strategy_slug("n100_guard_z") == "n100-guard-z"

    def test_no_change(self):
        assert strategy_slug("echotrend") == "echotrend"


class TestRotationDetection:
    """Integration test for main() rotation detection logic (mocked I/O)."""

    @patch("scripts.telegram_notify.load_subscribers", return_value=[])
    @patch("scripts.telegram_notify.send_telegram")
    @patch("scripts.telegram_notify.get_previous_signal_data")
    @patch("scripts.telegram_notify.get_current_signal")
    @patch("scripts.telegram_notify.DATA_DIR")
    def test_etf_rotation_detected(self, mock_data_dir, mock_current, mock_prev, mock_send, _):
        """ETF rotation (rotation_reason present) triggers notification."""
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake", "TELEGRAM_CHAT_ID": "123"}):
            import importlib
            import scripts.telegram_notify as notify_mod
            importlib.reload(notify_mod)
            # Re-patch after reload
            notify_mod.TELEGRAM_BOT_TOKEN = "fake"
            notify_mod.TELEGRAM_CHAT_ID = "123"

            mock_prev.return_value = {
                "action": "hold",
                "current_etf": "159660",
                "holding": "纳指ETF汇添富",
            }
            mock_current.return_value = {
                "strategy": {"name": "N100 Guard-Z", "code": "n100_guard_z", "symbol": "QQQ"},
                "current_signal": {
                    "action": "hold",
                    "current_etf": "159659",
                    "holding": "纳斯达克100ETF招商",
                    "current_zscore": 1.2,
                    "current_premium": 0.03,
                    "rotation_reason": {
                        "old_etf": "159660",
                        "new_etf": "159659",
                        "old_name": "纳指ETF汇添富",
                        "new_name": "纳斯达克100ETF招商",
                        "old_zscore": 2.5,
                        "new_zscore": 1.2,
                        "zscore_diff": 1.3,
                        "zscore_threshold": 1.0,
                        "old_premium": 0.06,
                        "new_premium": 0.03,
                        "premium_diff": 0.03,
                        "premium_threshold": 0.0016,
                        "holding_days": 15,
                        "holding_days_threshold": 10,
                    },
                },
            }

            mock_sdir = MagicMock()
            mock_sdir.name = "n100_guard_z"
            mock_sdir.is_dir.return_value = True
            mock_sdir.__truediv__ = lambda self, x: MagicMock(exists=lambda: True)
            mock_data_dir.iterdir.return_value = [mock_sdir]

            notify_mod.get_current_signal = mock_current
            notify_mod.get_previous_signal_data = mock_prev
            notify_mod.send_telegram = mock_send
            notify_mod.load_subscribers = lambda: []
            notify_mod.DATA_DIR = mock_data_dir

            notify_mod.main()
            mock_send.assert_called_once()
            call_msg = mock_send.call_args[0][0]
            assert "ETF Rotation" in call_msg
            assert "159660 → 159659" in call_msg
            assert "差值 1.30 > 阈值 1.0" in call_msg

    @patch("scripts.telegram_notify.load_subscribers", return_value=[])
    @patch("scripts.telegram_notify.send_telegram")
    @patch("scripts.telegram_notify.get_previous_signal_data")
    @patch("scripts.telegram_notify.get_current_signal")
    @patch("scripts.telegram_notify.DATA_DIR")
    def test_no_rotation_same_etf(self, mock_data_dir, mock_current, mock_prev, mock_send, _):
        """Same ETF, same action = no notification."""
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake", "TELEGRAM_CHAT_ID": "123"}):
            import importlib
            import scripts.telegram_notify as notify_mod
            importlib.reload(notify_mod)
            notify_mod.TELEGRAM_BOT_TOKEN = "fake"
            notify_mod.TELEGRAM_CHAT_ID = "123"

            mock_prev.return_value = {
                "action": "hold",
                "current_etf": "159660",
                "holding": "纳指ETF汇添富",
            }
            mock_current.return_value = {
                "strategy": {"name": "N100 Guard-Z", "code": "n100_guard_z", "symbol": "QQQ"},
                "current_signal": {
                    "action": "hold",
                    "current_etf": "159660",
                    "holding": "纳指ETF汇添富",
                    "current_zscore": 1.2,
                    "current_premium": 0.03,
                },
            }

            mock_sdir = MagicMock()
            mock_sdir.name = "n100_guard_z"
            mock_sdir.is_dir.return_value = True
            mock_sdir.__truediv__ = lambda self, x: MagicMock(exists=lambda: True)
            mock_data_dir.iterdir.return_value = [mock_sdir]

            notify_mod.get_current_signal = mock_current
            notify_mod.get_previous_signal_data = mock_prev
            notify_mod.send_telegram = mock_send
            notify_mod.load_subscribers = lambda: []
            notify_mod.DATA_DIR = mock_data_dir

            notify_mod.main()
            mock_send.assert_not_called()
