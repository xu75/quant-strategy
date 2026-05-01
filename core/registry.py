"""Strategy registry - discovers and loads strategy plugins.

Scans strategies/*/manifest.yaml, validates interface compliance,
and provides StrategyAdapter objects for the runner.
"""

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml


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
    manifest_path: Path = field(default_factory=lambda: Path("."))

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

    required = ["id", "name", "version", "description", "config", "display", "launch_date"]
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(f"manifest {manifest_path} missing required fields: {missing}")

    config = data["config"]
    config_required = ["timeframe", "symbol"]
    config_missing = [k for k in config_required if k not in config]
    if config_missing:
        raise ValueError(f"manifest {manifest_path} config missing: {config_missing}")

    return StrategyManifest(
        id=data["id"],
        name=data["name"],
        version=data["version"],
        description=data["description"],
        config=data["config"],
        display=data["display"],
        launch_date=data["launch_date"],
        enabled=data.get("enabled", True),
        manifest_path=manifest_path,
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
    )


def discover_strategies(
    strategies_dir: Path | None = None,
) -> list[StrategyManifest]:
    """Scan strategies directory for enabled strategy manifests.

    Args:
        strategies_dir: Path to strategies/ directory.
            Defaults to <project_root>/strategies/.

    Returns:
        List of validated, enabled StrategyManifest objects.
    """
    if strategies_dir is None:
        strategies_dir = Path(__file__).parent.parent / "strategies"

    manifests = []
    for manifest_path in sorted(strategies_dir.glob("*/manifest.yaml")):
        try:
            manifest = validate_manifest(manifest_path)
            if manifest.enabled:
                manifests.append(manifest)
        except (ValueError, yaml.YAMLError) as e:
            print(f"[Registry] Skipping {manifest_path}: {e}")

    return manifests
