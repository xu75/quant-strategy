from __future__ import annotations

"""Strategy state persistence for daily-signal incremental mode.

Handles state.json read/write, config hashing, and fail-closed validation.
"""

import hashlib
import json
from pathlib import Path

import pandas as pd


def config_hash(config_dict: dict) -> str:
    """Deterministic hash of strategy config for change detection."""
    raw = json.dumps(config_dict, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def load_state(state_path: Path) -> dict | None:
    """Load state.json, returning None if missing or corrupt."""
    if not state_path.exists():
        return None
    try:
        with open(state_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_state(state_path: Path, state: dict) -> None:
    """Atomically write state.json."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, default=str)
    tmp.replace(state_path)


def validate_state_for_resume(
    state: dict,
    strategy_id: str,
    strategy_version: str,
    current_config_hash: str,
    latest_data_ts: pd.Timestamp,
    warmup_bars: int,
    bar_interval_hours: float = 1.0,
) -> str | None:
    """Validate whether a saved state is safe to resume from.

    Returns None if valid, or an error message describing why resume
    is unsafe (fail-closed).
    """
    if state.get("strategy_id") != strategy_id:
        return f"strategy_id mismatch: state={state.get('strategy_id')}, current={strategy_id}"

    if state.get("strategy_version") != strategy_version:
        return f"strategy_version mismatch: state={state.get('strategy_version')}, current={strategy_version}"

    if state.get("config_hash") != current_config_hash:
        return f"config_hash mismatch: state={state.get('config_hash')}, current={current_config_hash}"

    watermark_str = state.get("watermark")
    if not watermark_str:
        return "missing watermark in state"

    watermark = pd.Timestamp(watermark_str)
    if watermark.tzinfo is None:
        watermark = watermark.tz_localize("UTC")

    gap = latest_data_ts - watermark
    max_gap_hours = warmup_bars * bar_interval_hours
    if gap.total_seconds() / 3600 > max_gap_hours:
        return (
            f"watermark gap too large: {gap} exceeds warmup window "
            f"({warmup_bars} bars * {bar_interval_hours}h = {max_gap_hours}h)"
        )

    if gap.total_seconds() < 0:
        return f"watermark {watermark} is ahead of latest data {latest_data_ts}"

    return None
