from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.registry import discover_strategies, validate_manifest


PROJECT_ROOT = Path(__file__).parent.parent
STRATEGIES_DIR = PROJECT_ROOT / "strategies"
SITE_PAGES = PROJECT_ROOT / "site" / "src" / "pages"


def _load_manifest(strategy_id: str) -> dict:
    return yaml.safe_load((STRATEGIES_DIR / strategy_id / "manifest.yaml").read_text())


def test_retracted_strategies_are_not_discovered_for_ci_pipeline():
    """CI runs run_all_strategies(), which is driven by discover_strategies()."""
    discovered_ids = {manifest.id for manifest in discover_strategies(STRATEGIES_DIR)}

    assert _load_manifest("echotrend_240")["status"] == "retracted"
    assert "echotrend_240" not in discovered_ids


def test_deprecated_or_retracted_manifest_requires_audit_fields():
    for manifest_path in sorted(STRATEGIES_DIR.glob("*/manifest.yaml")):
        data = yaml.safe_load(manifest_path.read_text())
        if data.get("status", "active") in {"deprecated", "retracted"}:
            assert data.get("status_reason"), f"{manifest_path}: missing status_reason"
            assert data.get("status_date"), f"{manifest_path}: missing status_date"


def test_registry_rejects_retracted_manifest_without_audit_fields(tmp_path):
    data = _load_manifest("btc_ma_trend")
    data["id"] = "bad_lifecycle"
    data["status"] = "retracted"
    data["enabled"] = False
    data.pop("status_reason", None)
    data.pop("status_date", None)

    strategy_dir = tmp_path / "bad_lifecycle"
    strategy_dir.mkdir()
    manifest_path = strategy_dir / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(data))

    with pytest.raises(ValueError, match="status_reason"):
        validate_manifest(manifest_path)


def test_homepage_retracted_card_has_grey_stamp_and_muted_live_values():
    source = (SITE_PAGES / "index.astro").read_text()

    assert "retracted-card" in source
    assert "retracted-stamp" in source
    assert "retracted-muted" in source
    assert "Archived Snapshot" in source


def test_echotrend_backtest_page_displays_lifecycle_banner():
    source = (SITE_PAGES / "backtest" / "echotrend-240.astro").read_text()

    assert "StrategyStatusBanner" in source
    assert "manifest.status" in source
