---
feature_ids: []
topics:
  - notifications
  - trendlock
  - telegram
  - webhook
doc_kind: review-request
created: 2026-09-15
---

# Review Request: Detect binary position transitions

Review-Target-ID: fix-binary-position-notifications
Branch: `fix/binary-position-notifications`
Implementation SHA: `278c6ab3b7fabbdeb90bd5a6999f96530011321e`

## What

The notifier now detects entry and exit events for binary strategies from the
durable `position.in_position` transition. The existing action comparison is
retained only for legacy snapshots without a boolean position contract.

The Telegram message and webhook payload explicitly label an inferred event's
price as the latest snapshot price, not an execution price. The patch also adds
regression tests and a production-history bug report.

## Why

The daily workflow can sample a 4-hour strategy only after its transient
`buy`/`sell` action has returned to `hold`. In July 2026 this made complete
TrendLock Plus entry and exit events invisible to both Telegram and webhook
delivery.

## Original Requirements

> “7月份 Regime 翻转时……tg bot 也只有 EchTrend 给了消息，TrendLock Plus
> 没有在 tg bot 和 webhook 里面发消息。所以这个自动通知机制，感觉还是有问题。”

- Source: Cat Café thread `thread_mrfq8n304polwg7x`, message
  `0001788800201311-000008-0b3958cb`
- Please verify that the implementation addresses the operator-visible gap in
  both configured delivery channels.

## Tradeoff

This is a bounded correction to the existing snapshot-diff architecture. It
does not add a durable event outbox, change strategy rules, change the workflow
schedule, or alter market-data resampling. Missing position fields retain the
old action-based behavior for compatibility.

## Architecture Ownership

Architecture cell: strategy notification delivery
Map delta: none
Why: the patch corrects event detection inside the existing notifier and does
not create a new store, queue, router, adapter, dispatcher, or binding.

## Open Questions

### Technical OQ

- Is the boolean contract check strict enough without suppressing a valid
  action from any current binary strategy?
- Are inferred event semantics sufficiently clear in both Telegram and webhook
  payloads?
- Does the change preserve the continuous-exposure path and avoid duplicate
  notifications when action and position change together?

### Value OQ

None.

## Fresh-Context Findings

Agent: [砚砚/gpt-5.6-sol🐾]
SHA scanned: pre-commit diff based on `6453933`
Total findings: 1 (0 P1, 1 P2, 0 P3)

| # | Finding | Author disposition | Status |
|---|---|---|---|
| FC-1 | A complete, stable position contract still fell through to transient action detection and could false-positive. | Fixed in `278c6ab`; added a red/green regression test and gated fallback on a missing contract. | Fixed |

Finding generator only; this is not an approval verdict. Formal reviewer delta
should mark findings as `FC:covered`, `FC:new`, or `FC:N/A`.

## Next Action

Perform an independent review of `git diff origin/main...HEAD`, rerun the
validation commands, and return an explicit APPROVE or REQUEST-CHANGES verdict
for the reviewed HEAD SHA.

## Review Sandbox

- Path: `/tmp/cat-cafe-review/fix-binary-position-notifications/opus`
- Bootstrap: `git worktree add --detach <path> fix/binary-position-notifications`
- Start command: none; this is a Python script/test change with no server.
- Ports: not applicable.
- Python: use the repository's development environment or install
  `.[dev]` in an isolated virtual environment.

## Self-check evidence

### Spec compliance

- Real July entry (`false -> true`) and exit (`true -> false`) each generate
  exactly one TrendLock Plus action notification.
- Telegram and webhook formatters consume the same generated event.
- A stable boolean position suppresses transient action fallback.
- Continuous-exposure strategies remain on their existing exposure path.
- No test contacted Telegram or a webhook endpoint.
- No UI, design, root media artifact, Redis, production data, or strategy rule
  changed.

### Validation

```bash
python -m pytest tests/test_notify.py -q
# 22 passed

python -m pytest tests/ -q
# 251 passed, 4 pre-existing pandas resample warnings

python -m compileall -q scripts/telegram_notify.py tests/test_notify.py
git diff --check
# exit 0
```

Dogfood: replaying actual snapshots `5b2cf4c -> 8765429` produced one BUY;
`7abc55b -> 6a668db` produced one SELL, both with
`detection_source=position_transition`.

## Related documents

- `docs/bug-report/trendlock-binary-notification-gap/bug-report.md`

