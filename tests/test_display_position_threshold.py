"""Regression tests for display position threshold (<=3% exposure = 空仓).

Tests the is_display_in_position helper directly, ensuring both runner paths
use the same threshold and backtest semantics remain unaffected.
"""
from __future__ import annotations

import inspect

from core.runner import is_display_in_position, DISPLAY_POSITION_THRESHOLD


def test_below_threshold_shows_not_in_position():
    assert is_display_in_position(0.0295) is False


def test_at_threshold_shows_not_in_position():
    assert is_display_in_position(0.03) is False


def test_above_threshold_shows_in_position():
    assert is_display_in_position(0.0301) is True


def test_none_returns_fallback():
    assert is_display_in_position(None, fallback=False) is False
    assert is_display_in_position(None, fallback=True) is True


def test_threshold_constant_is_3pct():
    assert DISPLAY_POSITION_THRESHOLD == 0.03


def test_backtest_has_open_uses_1pct_threshold():
    """BacktestResult.has_open_position must use 0.01, not display threshold."""
    from strategies.echotrend_240_v3 import signal as v3_signal
    source = inspect.getsource(v3_signal.run_backtest)
    assert "final_exposure > 0.01" in source


def test_runner_full_backtest_uses_helper():
    """full-backtest path must call is_display_in_position."""
    from core import runner
    source = inspect.getsource(runner._run_full_backtest)
    assert "is_display_in_position" in source


def test_runner_daily_signal_uses_helper():
    """daily-signal path must call is_display_in_position."""
    from core import runner
    source = inspect.getsource(runner._run_daily_signal)
    assert "is_display_in_position" in source
