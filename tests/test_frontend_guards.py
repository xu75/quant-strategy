"""Frontend template guard tests.

Ensures strategy pages use shared components instead of hand-writing
markup that should only live in reusable components.
"""

import glob
import os
import re

import pytest

STRATEGY_PAGES_DIR = os.path.join(
    os.path.dirname(__file__), "..", "site", "src", "pages", "strategy"
)

COMPONENT_DIR = os.path.join(
    os.path.dirname(__file__), "..", "site", "src", "components"
)

FORBIDDEN_IN_PAGES = [
    (r'id="period-tabs"', "period-tabs element must come from PerformanceSummary component"),
    (r'id="perf-', "perf-* metric elements must come from PerformanceSummary component"),
    (r'data-i18n="strategy\.view_backtest"', "backtest link must come from PerformanceSummary component"),
    (r'data-i18n="label\.(return|buyhold|drawdown|sharpe|winrate|trades|avghold)"', "metric labels must come from PerformanceSummary component"),
]


def _get_strategy_pages():
    pattern = os.path.join(STRATEGY_PAGES_DIR, "*.astro")
    return sorted(glob.glob(pattern))


@pytest.mark.parametrize("page_path", _get_strategy_pages(), ids=lambda p: os.path.basename(p))
def test_no_hand_written_performance_markup(page_path):
    """Strategy pages must not contain performance section markup directly."""
    content = open(page_path).read()
    violations = []
    for pattern, msg in FORBIDDEN_IN_PAGES:
        if re.search(pattern, content):
            violations.append(msg)
    assert not violations, (
        f"{os.path.basename(page_path)} contains hand-written markup that "
        f"should only exist in shared components:\n  - " + "\n  - ".join(violations)
    )


def test_performance_summary_component_exists():
    """PerformanceSummary.astro must exist as the single source of truth."""
    comp = os.path.join(COMPONENT_DIR, "PerformanceSummary.astro")
    assert os.path.isfile(comp), "PerformanceSummary.astro component is missing"


def test_strategy_pages_import_performance_summary():
    """All strategy pages must import and use the shared PerformanceSummary component."""
    pages = _get_strategy_pages()
    assert pages, "No strategy pages found"
    for page_path in pages:
        content = open(page_path).read()
        assert "PerformanceSummary" in content, (
            f"{os.path.basename(page_path)} does not use PerformanceSummary component"
        )
