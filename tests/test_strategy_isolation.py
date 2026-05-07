"""Strategy isolation tests — enforce boundaries between platform and strategy layers."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).parent.parent
STRATEGIES_DIR = PROJECT_ROOT / "strategies"
PIPELINE_DIR = PROJECT_ROOT / "pipeline"
CORE_DIR = PROJECT_ROOT / "core"

SAFE_ID_RE = re.compile(r"^[a-z0-9_]+$")
SAFE_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


def _load_all_manifests() -> list[tuple[Path, dict]]:
    results = []
    for p in sorted(STRATEGIES_DIR.glob("*/manifest.yaml")):
        with open(p) as f:
            results.append((p, yaml.safe_load(f)))
    return results


def _python_files(directory: Path) -> list[Path]:
    return sorted(directory.rglob("*.py"))


class TestManifestIsolation:
    """Manifest-level isolation: ID format, dirname match, uniqueness, coverage fields."""

    @pytest.fixture(scope="class")
    def manifests(self):
        return _load_all_manifests()

    def test_id_matches_directory_name(self, manifests):
        for path, data in manifests:
            assert data["id"] == path.parent.name, (
                f"{path}: id '{data['id']}' != directory '{path.parent.name}'"
            )

    def test_id_safe_characters(self, manifests):
        for path, data in manifests:
            assert SAFE_ID_RE.match(data["id"]), (
                f"{path}: id '{data['id']}' must match [a-z0-9_]+"
            )

    def test_slug_safe_characters(self, manifests):
        for path, data in manifests:
            slug = data.get("display", {}).get("slug", data["id"])
            assert SAFE_SLUG_RE.match(slug), (
                f"{path}: slug '{slug}' must match [a-z0-9-]+"
            )

    def test_no_duplicate_ids(self, manifests):
        ids = [data["id"] for _, data in manifests]
        assert len(ids) == len(set(ids)), f"Duplicate strategy IDs: {ids}"

    def test_no_duplicate_slugs(self, manifests):
        slugs = [data.get("display", {}).get("slug", data["id"]) for _, data in manifests]
        assert len(slugs) == len(set(slugs)), f"Duplicate strategy slugs: {slugs}"

    def test_min_lookback_years_declared(self, manifests):
        for path, data in manifests:
            if not data.get("enabled", True):
                continue
            assert "min_lookback_years" in data, (
                f"{path}: missing 'min_lookback_years' (required by coverage guardrail)"
            )
            assert isinstance(data["min_lookback_years"], int) and data["min_lookback_years"] >= 1

    def test_warmup_bars_declared(self, manifests):
        for path, data in manifests:
            if not data.get("enabled", True):
                continue
            assert "warmup_bars" in data, (
                f"{path}: missing 'warmup_bars' (required by coverage guardrail)"
            )
            assert isinstance(data["warmup_bars"], int) and data["warmup_bars"] >= 1

    def test_data_sources_with_canonical_primary(self, manifests):
        for path, data in manifests:
            if not data.get("enabled", True):
                continue
            assert "data_sources" in data, (
                f"{path}: missing 'data_sources' — full-backtest requires canonical data"
            )
            primary_symbol = data["config"]["symbol"]
            found = any(
                src.get("symbol") == primary_symbol and src.get("local_file")
                for src in data["data_sources"].values()
            )
            assert found, (
                f"{path}: no data_source with local_file for primary symbol '{primary_symbol}'"
            )

    def test_status_field_valid(self, manifests):
        valid_statuses = ("active", "experimental", "deprecated", "retracted")
        for path, data in manifests:
            status = data.get("status", "active")
            assert status in valid_statuses, (
                f"{path}: status '{status}' must be one of {valid_statuses}"
            )

    def test_deprecated_retracted_must_be_disabled(self, manifests):
        for path, data in manifests:
            status = data.get("status", "active")
            if status in ("deprecated", "retracted"):
                assert not data.get("enabled", True), (
                    f"{path}: status '{status}' requires enabled: false"
                )


class TestImportBoundaries:
    """Platform code must not import strategy-specific modules."""

    def _imports_strategies(self, filepath: Path) -> list[str]:
        violations = []
        source = filepath.read_text()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("strategies."):
                        violations.append(f"{filepath}:{node.lineno} imports {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("strategies."):
                    violations.append(f"{filepath}:{node.lineno} imports from {node.module}")
        return violations

    def test_pipeline_does_not_import_strategies(self):
        violations = []
        for py in _python_files(PIPELINE_DIR):
            violations.extend(self._imports_strategies(py))
        assert not violations, f"pipeline/ imports strategies:\n" + "\n".join(violations)

    def test_core_does_not_import_strategies(self):
        violations = []
        for py in _python_files(CORE_DIR):
            violations.extend(self._imports_strategies(py))
        assert not violations, f"core/ imports strategies:\n" + "\n".join(violations)

    def test_no_cross_strategy_imports(self):
        """Each strategy may only import from its own directory, not other strategies."""
        violations = []
        for manifest_path, data in _load_all_manifests():
            strategy_id = data["id"]
            strategy_dir = manifest_path.parent
            for py in _python_files(strategy_dir):
                source = py.read_text()
                try:
                    tree = ast.parse(source)
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    module = None
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name.startswith("strategies."):
                                module = alias.name
                    elif isinstance(node, ast.ImportFrom):
                        if node.module and node.module.startswith("strategies."):
                            module = node.module
                    if module is None:
                        continue
                    parts = module.split(".")
                    if len(parts) >= 2 and parts[1] != strategy_id:
                        violations.append(
                            f"{py}:{node.lineno} strategy '{strategy_id}' imports from '{module}'"
                        )
        assert not violations, (
            f"Cross-strategy imports found:\n" + "\n".join(violations)
        )


class TestOutputPathIsolation:
    """Data and chart output paths must be namespaced by strategy ID."""

    def test_data_dirs_match_strategy_ids(self):
        data_dir = PROJECT_ROOT / "data"
        if not data_dir.exists():
            pytest.skip("data/ not present")
        manifest_ids = {data["id"] for _, data in _load_all_manifests()}
        platform_dirs = {"market"}
        for child in data_dir.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                if child.name in platform_dirs:
                    continue
                assert child.name in manifest_ids, (
                    f"data/{child.name}/ has no matching strategy manifest"
                )

    def test_chart_dirs_match_strategy_ids(self):
        charts_dir = PROJECT_ROOT / "site" / "public" / "charts"
        if not charts_dir.exists():
            pytest.skip("site/public/charts/ not present")
        manifest_ids = {data["id"] for _, data in _load_all_manifests()}
        for child in charts_dir.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                assert child.name in manifest_ids, (
                    f"site/public/charts/{child.name}/ has no matching strategy manifest"
                )
