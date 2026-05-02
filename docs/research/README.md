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
