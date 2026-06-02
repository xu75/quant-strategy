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


class TestEtfPoolConsistency:
    """ETF pool config must be single source of truth — manifest/signal/pipeline must agree."""

    def test_etf_pool_yaml_is_authoritative(self):
        pool_path = STRATEGIES_DIR / "n100_guard_z" / "etf_pool.yaml"
        if not pool_path.exists():
            pytest.skip("etf_pool.yaml not present")
        with open(pool_path) as f:
            pool = yaml.safe_load(f)
        codes = {etf["code"] for etf in pool["etfs"]}
        assert len(codes) == len(pool["etfs"]), "Duplicate codes in etf_pool.yaml"
        for etf in pool["etfs"]:
            assert etf["tracking_index"] == "纳斯达克100指数", (
                f"{etf['code']}: tracking_index must be 纳斯达克100指数"
            )

    def test_manifest_etf_pool_matches_config(self):
        pool_path = STRATEGIES_DIR / "n100_guard_z" / "etf_pool.yaml"
        manifest_path = STRATEGIES_DIR / "n100_guard_z" / "manifest.yaml"
        if not pool_path.exists():
            pytest.skip("etf_pool.yaml not present")
        with open(pool_path) as f:
            pool = yaml.safe_load(f)
        with open(manifest_path) as f:
            manifest = yaml.safe_load(f)
        pool_codes = {etf["code"] for etf in pool["etfs"]}
        manifest_codes = {item["code"] for item in manifest["etf_pool"]["risk_on"]}
        assert pool_codes == manifest_codes, (
            f"manifest etf_pool.risk_on codes don't match etf_pool.yaml: "
            f"missing={pool_codes - manifest_codes}, extra={manifest_codes - pool_codes}"
        )

    def test_manifest_data_sources_cover_pool(self):
        pool_path = STRATEGIES_DIR / "n100_guard_z" / "etf_pool.yaml"
        manifest_path = STRATEGIES_DIR / "n100_guard_z" / "manifest.yaml"
        if not pool_path.exists():
            pytest.skip("etf_pool.yaml not present")
        with open(pool_path) as f:
            pool = yaml.safe_load(f)
        with open(manifest_path) as f:
            manifest = yaml.safe_load(f)
        pool_codes = {etf["code"] for etf in pool["etfs"]}
        ds_symbols = {src["symbol"] for src in manifest["data_sources"].values()}
        missing = pool_codes - ds_symbols
        assert not missing, (
            f"manifest data_sources missing ETF entries: {missing}"
        )

    def test_manifest_names_match_pool(self):
        """ETF names in manifest.etf_pool and manifest.data_sources must match etf_pool.yaml."""
        pool_path = STRATEGIES_DIR / "n100_guard_z" / "etf_pool.yaml"
        manifest_path = STRATEGIES_DIR / "n100_guard_z" / "manifest.yaml"
        if not pool_path.exists():
            pytest.skip("etf_pool.yaml not present")
        with open(pool_path) as f:
            pool = yaml.safe_load(f)
        with open(manifest_path) as f:
            manifest = yaml.safe_load(f)

        pool_names = {etf["code"]: etf["official_short_name"] for etf in pool["etfs"]}

        # Check manifest.etf_pool.risk_on names
        for item in manifest["etf_pool"]["risk_on"]:
            code = item["code"]
            manifest_name = item["name"]
            pool_name = pool_names.get(code)
            assert manifest_name == pool_name, (
                f"manifest.etf_pool.risk_on[{code}].name = '{manifest_name}' "
                f"!= etf_pool.yaml official_short_name = '{pool_name}'"
            )

        # Check manifest.data_sources notes contain correct names
        for key, src in manifest["data_sources"].items():
            if not key.startswith("etf_"):
                continue
            code = src["symbol"]
            if code not in pool_names:
                continue
            note = src.get("note", "")
            pool_name = pool_names[code]
            assert pool_name in note, (
                f"manifest.data_sources.{key}.note does not contain '{pool_name}' from etf_pool.yaml"
            )

    def test_manifest_listed_dates_match_pool(self):
        """ETF listed dates in manifest.etf_pool must match etf_pool.yaml listed_date (YYYY-MM)."""
        pool_path = STRATEGIES_DIR / "n100_guard_z" / "etf_pool.yaml"
        manifest_path = STRATEGIES_DIR / "n100_guard_z" / "manifest.yaml"
        if not pool_path.exists():
            pytest.skip("etf_pool.yaml not present")
        with open(pool_path) as f:
            pool = yaml.safe_load(f)
        with open(manifest_path) as f:
            manifest = yaml.safe_load(f)

        pool_dates = {etf["code"]: etf["listed_date"][:7] for etf in pool["etfs"]}

        for item in manifest["etf_pool"]["risk_on"]:
            code = item["code"]
            manifest_listed = item["listed"]
            pool_listed = pool_dates.get(code)
            assert manifest_listed == pool_listed, (
                f"manifest.etf_pool.risk_on[{code}].listed = '{manifest_listed}' "
                f"!= etf_pool.yaml listed_date[:7] = '{pool_listed}'"
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
