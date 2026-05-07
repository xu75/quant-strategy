from __future__ import annotations

"""Strategy registry - discovers and loads strategy plugins.

Scans strategies/*/manifest.yaml, validates interface compliance,
and provides StrategyAdapter objects for the runner.
"""

import importlib.util
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

_SAFE_ID_RE = re.compile(r"^[a-z0-9_]+$")
_SAFE_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


VALID_STATUSES = ("active", "experimental", "deprecated", "retracted")


@dataclass
class StrategyManifest:
    """Parsed strategy manifest metadata."""

    id: str
    name: str
    version: str
    description: str
    config: dict
    display: dict
    launch_date: str
    enabled: bool = True
    status: str = "active"
    status_reason: str = ""
    status_date: str = ""
    superseded_by: str = ""
    manifest_path: Path = field(default_factory=lambda: Path("."))
    _raw_data: dict = field(default_factory=dict, repr=False)

    @property
    def slug(self) -> str:
        return self.display.get("slug", self.id)

    @property
    def category(self) -> str:
        return self.display.get("category", "uncategorized")

    @property
    def short_desc(self) -> str:
        return self.display.get("short_desc", self.description)


@dataclass
class StrategyAdapter:
    """Bundles a strategy's config and callable interface for the runner."""

    manifest: StrategyManifest
    config: Any  # Strategy-specific StrategyConfig instance
    compute_signals: Callable
    get_current_signal: Callable
    get_filtered_df: Callable | None = None
    run_backtest: Callable | None = None
    export_engine_state: Callable | None = None
    import_engine_state: Callable | None = None
    run_incremental: Callable | None = None


def validate_manifest(manifest_path: Path) -> StrategyManifest:
    """Parse and validate a manifest.yaml file.

    Args:
        manifest_path: Path to manifest.yaml.

    Returns:
        Validated StrategyManifest.

    Raises:
        ValueError: If required fields are missing.
    """
    with open(manifest_path) as f:
        data = yaml.safe_load(f)

    required = ["id", "name", "version", "description", "config", "display", "launch_date", "min_lookback_years", "warmup_bars"]
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(f"manifest {manifest_path} missing required fields: {missing}")

    strategy_id = data["id"]
    if not _SAFE_ID_RE.match(strategy_id):
        raise ValueError(
            f"manifest {manifest_path}: id '{strategy_id}' must match [a-z0-9_]+"
        )

    dir_name = manifest_path.parent.name
    if strategy_id != dir_name:
        raise ValueError(
            f"manifest {manifest_path}: id '{strategy_id}' must match directory name '{dir_name}'"
        )

    slug = data["display"].get("slug", strategy_id)
    if not _SAFE_SLUG_RE.match(slug):
        raise ValueError(
            f"manifest {manifest_path}: slug '{slug}' must match [a-z0-9-]+"
        )

    config = data["config"]
    config_required = ["timeframe", "symbol"]
    config_missing = [k for k in config_required if k not in config]
    if config_missing:
        raise ValueError(f"manifest {manifest_path} config missing: {config_missing}")

    if "data_sources" not in data:
        raise ValueError(
            f"manifest {manifest_path}: missing 'data_sources' — full-backtest requires canonical data declaration"
        )
    primary_symbol = config["symbol"]
    primary_has_local = any(
        src.get("symbol") == primary_symbol and src.get("local_file")
        for src in data["data_sources"].values()
    )
    if not primary_has_local:
        raise ValueError(
            f"manifest {manifest_path}: data_sources must declare a local_file for primary symbol '{primary_symbol}'"
        )

    status = data.get("status", "active")
    if status not in VALID_STATUSES:
        raise ValueError(
            f"manifest {manifest_path}: status '{status}' must be one of {VALID_STATUSES}"
        )

    enabled = data.get("enabled", True)
    if status in ("deprecated", "retracted") and enabled:
        raise ValueError(
            f"manifest {manifest_path}: status '{status}' requires enabled: false"
        )

    return StrategyManifest(
        id=data["id"],
        name=data["name"],
        version=data["version"],
        description=data["description"],
        config=data["config"],
        display=data["display"],
        launch_date=data["launch_date"],
        enabled=enabled,
        status=status,
        status_reason=data.get("status_reason", ""),
        status_date=data.get("status_date", ""),
        superseded_by=data.get("superseded_by", ""),
        manifest_path=manifest_path,
        _raw_data=data,
    )


def load_strategy_module(manifest: StrategyManifest) -> StrategyAdapter:
    """Dynamically load a strategy's signal.py and build a StrategyAdapter.

    Args:
        manifest: Validated strategy manifest.

    Returns:
        StrategyAdapter with config and callable functions.

    Raises:
        ImportError: If signal.py cannot be loaded.
        AttributeError: If required interface is missing.
    """
    strategy_dir = manifest.manifest_path.parent
    module_path = strategy_dir / "signal.py"
    if not module_path.exists():
        raise ImportError(f"No signal.py found at {module_path}")

    # Dynamic import
    module_name = f"strategies.{manifest.id}.signal"
    if module_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    else:
        module = sys.modules[module_name]

    # Validate interface
    for attr in ("StrategyConfig", "compute_signals", "get_current_signal"):
        if not hasattr(module, attr):
            raise AttributeError(
                f"Strategy {manifest.id} signal.py missing required: {attr}"
            )

    # Build config from manifest, mapping display fields
    config_kwargs = dict(manifest.config)
    config_kwargs.setdefault("display_name", manifest.name)
    config_kwargs.setdefault("internal_code", manifest.id)
    config = module.StrategyConfig(**config_kwargs)

    return StrategyAdapter(
        manifest=manifest,
        config=config,
        compute_signals=module.compute_signals,
        get_current_signal=module.get_current_signal,
        get_filtered_df=getattr(module, "get_filtered_df", None),
        run_backtest=getattr(module, "run_backtest", None),
        export_engine_state=getattr(module, "export_engine_state", None),
        import_engine_state=getattr(module, "import_engine_state", None),
        run_incremental=getattr(module, "run_incremental", None),
    )


def discover_strategies(
    strategies_dir: Path | None = None,
    *,
    strict: bool = True,
) -> list[StrategyManifest]:
    """Scan strategies directory for enabled strategy manifests.

    Args:
        strategies_dir: Path to strategies/ directory.
            Defaults to <project_root>/strategies/.
        strict: If True (default), raise on any validation error.
            If False, skip invalid manifests with a warning.

    Returns:
        List of validated, enabled StrategyManifest objects.
    """
    if strategies_dir is None:
        strategies_dir = Path(__file__).parent.parent / "strategies"

    manifests = []
    errors = []
    seen_ids: set[str] = set()
    seen_slugs: set[str] = set()
    for manifest_path in sorted(strategies_dir.glob("*/manifest.yaml")):
        try:
            manifest = validate_manifest(manifest_path)
            if not manifest.enabled:
                continue
            if manifest.id in seen_ids:
                raise ValueError(f"duplicate strategy id: '{manifest.id}'")
            if manifest.slug in seen_slugs:
                raise ValueError(f"duplicate strategy slug: '{manifest.slug}'")
            seen_ids.add(manifest.id)
            seen_slugs.add(manifest.slug)
            manifests.append(manifest)
        except (ValueError, yaml.YAMLError) as e:
            if strict:
                errors.append(str(e))
            else:
                print(f"[Registry] Skipping {manifest_path}: {e}")

    if errors:
        raise ValueError(
            f"Strategy discovery failed with {len(errors)} error(s):\n"
            + "\n".join(f"  - {e}" for e in errors)
        )

    return manifests
