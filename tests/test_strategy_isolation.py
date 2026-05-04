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
    """Manifest-level isolation: ID format, dirname match, uniqueness."""

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
        for child in data_dir.iterdir():
            if child.is_dir() and not child.name.startswith("."):
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
