"""Frontend guards for the continuous-exposure display contract (V3-style).

Regression coverage for the review findings:
  P1-2: the homepage card must NOT surface the audit-only `action` field
        (which rendered a bare "Signal: sell"). Continuous strategies show
        the model's current_exposure instead.
  P1-3 + i18n: the actionable instruction must be present with an unambiguous
        "total portfolio" denominator, and its translation keys must exist in
        both en and zh so Chinese mode never falls back to English.
"""
from __future__ import annotations

import os
import re

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
V3_MANIFEST = os.path.join(ROOT, "strategies", "echotrend_240_v3", "manifest.yaml")
SLUG_PAGE = os.path.join(ROOT, "site", "src", "pages", "strategy", "[slug].astro")
TRANSLATIONS = os.path.join(ROOT, "site", "src", "i18n", "translations.ts")

# Strategies whose latest.json carries current_exposure (continuous contract).
CONTINUOUS_STRATEGIES = ["echotrend_240_v3"]


def _load_manifest(path):
    with open(path) as f:
        return yaml.safe_load(f)


def test_v3_card_does_not_surface_bare_action():
    """P1-2: homepage card_fields must not bind the audit-only `action`."""
    m = _load_manifest(V3_MANIFEST)
    card_fields = m.get("page", {}).get("card_fields", [])
    keys = {f.get("key") for f in card_fields}
    assert "action" not in keys, (
        "V3 homepage card still binds `action` — renders a bare SELL. "
        "Use current_exposure (percent) instead."
    )
    assert "current_exposure" in keys, (
        "V3 homepage card should surface current_exposure for the continuous contract."
    )


def test_v3_card_exposure_field_is_percent():
    m = _load_manifest(V3_MANIFEST)
    card_fields = m.get("page", {}).get("card_fields", [])
    exp = [f for f in card_fields if f.get("key") == "current_exposure"]
    assert exp and exp[0].get("format") == "percent", (
        "current_exposure card field must use percent format."
    )


def test_slug_page_has_total_portfolio_instruction():
    """P1-3: the detail page must give a total-portfolio-denominated instruction."""
    content = open(SLUG_PAGE).read()
    assert 'data-i18n="exposure.instruction"' in content, (
        "Detail page missing the exposure.instruction element."
    )
    # The SSR default text must state the denominator explicitly.
    assert "total portfolio" in content, (
        "Instruction must say 'total portfolio' (unambiguous denominator)."
    )
    # The ambiguous 'Hold X% of your MSTR allocation' phrasing must be gone.
    assert "of your" not in content or "allocation" not in content, (
        "Ambiguous 'X% of your ... allocation' phrasing still present."
    )


def test_exposure_instruction_i18n_keys_exist_both_langs():
    """i18n: new user-facing keys must be translated in en AND zh."""
    content = open(TRANSLATIONS).read()
    required = [
        "exposure.model_position",
        "exposure.target",
        "exposure.state.reducing",
        "exposure.state.increasing",
        "exposure.state.holding",
        "exposure.action_prefix",
        "exposure.instruction",
    ]
    # Each key must appear at least twice (once per language block).
    for key in required:
        count = len(re.findall(re.escape(f"'{key}'"), content))
        assert count >= 2, (
            f"i18n key '{key}' must exist in both en and zh blocks (found {count})."
        )
    # The zh instruction must be actual Chinese (contains 总组合), not English.
    assert "总组合" in content, "zh instruction should read '将 {sym} 调整至总组合的 {v}%'."


def test_instruction_placeholders_consistent():
    """The interpolation placeholders must match between en and zh."""
    content = open(TRANSLATIONS).read()
    instrs = re.findall(r"'exposure\.instruction':\s*'([^']*)'", content)
    assert len(instrs) == 2, f"expected 2 instruction strings, found {len(instrs)}"
    for s in instrs:
        assert "{sym}" in s and "{v}" in s, f"instruction '{s}' missing placeholders"


def test_instruction_data_attrs_map_to_dataset_keys():
    """Interpolation lookup uses el.dataset[name], and HTML camelCases
    `data-<x>` -> dataset.<x> only for single-segment names. The instruction
    placeholders {sym}/{v} must therefore be fed by `data-sym`/`data-v`
    (NOT `data-i18n-sym`, which would become dataset.i18nSym and never match).
    """
    content = open(SLUG_PAGE).read()
    m = re.search(r'data-i18n="exposure\.instruction"[^>]*', content)
    assert m, "instruction element not found"
    tag = m.group(0)
    assert "data-sym=" in tag, "instruction must feed {sym} via data-sym"
    assert "data-v=" in tag, "instruction must feed {v} via data-v"
    # The broken prefixed form must not be used.
    assert "data-i18n-sym" not in tag and "data-i18n-v" not in tag, (
        "data-i18n-<x> maps to dataset.i18nX and won't resolve {x} placeholders."
    )


def test_layout_interpolation_present():
    """Layout runtime must interpolate {placeholder} from dataset, else zh mode
    would show literal {sym}/{v}."""
    layout = open(os.path.join(ROOT, "site", "src", "layouts", "Layout.astro")).read()
    assert "replace(/\\{(\\w+)\\}/g" in layout or "{(\\w+)}" in layout, (
        "Layout applyLang must interpolate {placeholder} tokens from dataset."
    )
