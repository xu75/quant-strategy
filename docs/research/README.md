---
feature_ids: []
topics: ["research-doc", "strategy-governance", "documentation"]
doc_kind: "guide"
created: "2026-05-02"
---

# Strategy Research Docs Standard

## Decision

Use a hybrid publication model for strategy research:

1. GitHub markdown is the canonical source of truth (versioned and reviewable).
2. Website provides a concise public summary page.
3. External long-form docs (WPS/Notion/Google Docs) are optional source references, not canonical truth.

## Required Per Strategy

For every enabled strategy manifest:

1. `research.paper_title`
2. `research.website_path`
3. `research.canonical_doc`
4. `research.source_docx` (recommended when original paper is delivered as file)
5. `research.external_doc_url` (optional, only for external-hosted originals)

## Canonical Doc Location

Store canonical strategy papers in:

- `docs/research/strategies/<strategy_id>.md`

Use:

- `docs/research/strategies/TEMPLATE.md`

## Workflow

1. Draft/update strategy research in canonical markdown.
2. Review with code/data changes in the same PR when possible.
3. Keep strategy webpage research section linked to both:
   - website summary page
   - canonical GitHub markdown
4. Keep external source link for provenance.

## Quality Checklist

1. Strategy objective and constraints are explicit.
2. Data source, timeframe, fee assumptions are explicit.
3. Results include both performance and risk metrics.
4. Known limitations are listed.
5. Reproducibility paths (scripts/files) are listed.

## Strategy Launch Checklist

Every new strategy or strategy version must complete the following before `enabled: true`.

### 1. Strategy Definition

- [ ] Strategy directory: `strategies/<strategy_id>/` with `__init__.py`, `signal.py`, `manifest.yaml`
- [ ] `manifest.yaml`: id, version, parameters, display slug, research links
- [ ] `signal.py`: implements `StrategyConfig`, `compute_signals`, `get_current_signal`
- [ ] Unit tests covering signal edge cases

### 2. Research Documentation

- [ ] Research doc: `docs/research/strategies/<strategy_id>.md` following `TEMPLATE.md`
- [ ] Quality Checklist (above 5 items) all pass
- [ ] Decision rationale recorded (why this config was chosen)

### 3. Pre-Launch Validation

- [ ] Trade-level diff vs baseline (confirm where improvement comes from)
- [ ] Segment analysis: bull / bear / sideways periods separately
- [ ] Slippage stress test: fee=0.1% / 0.2% / 0.3%, confirm conclusion stability
- [ ] Production semantic alignment: either match exactly, or document execution/model differences and validate relative conclusions under both models

### 4. Website & Display

- [ ] Strategy page: `site/src/pages/strategy/<slug>.astro`
- [ ] Research page: `site/src/pages/research/<slug>.astro`
- [ ] Backtest page: `site/src/pages/backtest/<slug>.astro`
- [ ] Homepage strategy list updated

### 5. Operations

- [ ] Backtest data: `data/<strategy_id>/backtest.json` generated
- [ ] GitHub Actions: `run_strategy.yml` runs new strategy (or new workflow added)
- [ ] `enabled: true` in manifest (final switch)

### 6. Archival

- [ ] Sweep scripts and raw data committed
- [ ] Decision record in research doc (who recommended, who confirmed)
