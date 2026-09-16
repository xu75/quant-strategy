---
feature_ids: []
topics:
  - notifications
  - trendlock
  - telegram
  - webhook
doc_kind: bug-report
created: 2026-09-15
---

# TrendLock binary signal notification gap

## 1. Reporter and symptom

The operator observed that the July 2026 market transition produced an
EchoTrend Telegram message, while TrendLock 40 Plus produced neither a
Telegram nor a webhook notification.

## 2. Reproduction

The scheduled workflow ran once per day while TrendLock generated signals on
4-hour bars.

- Entry: the 2026-07-09 snapshot was `action=hold, in_position=false`; the
  2026-07-10 snapshot was `action=hold, in_position=true`.
- Exit: the 2026-07-13 snapshot was `action=hold, in_position=true`; the
  2026-07-14 snapshot was `action=hold, in_position=false`.
- Expected: one BUY notification for the entry and one SELL notification for
  the exit, delivered through every configured channel.
- Actual: `telegram_notify.py` compared only `current_signal.action`, so both
  changes appeared to be `hold -> hold` and were discarded.

GitHub Actions run `29088857700` logged `No signal changes detected` after the
entry. Run `29324184461` detected only the unrelated EchoTrend action and sent
one Telegram message.

## 3. Root cause

For binary strategies, `action` is a one-bar event label. It returns to `hold`
on the next bar, while `position.in_position` persists for the duration of the
trade. Comparing a transient 4-hour label from daily snapshots creates a
sampling gap: a complete entry or exit can occur between workflow runs without
ever appearing as the latest `action`.

The missing webhook on 2026-07-14 had a second, independent cause: the endpoint
returned HTTP 200 with `data=false`, which the old sender treated as success.
That delivery bug was fixed on main in commit
[`d5385e6bd7ded614c1649b6681052d436db5758c`](https://github.com/xu75/quant-strategy/commit/d5385e6bd7ded614c1649b6681052d436db5758c);
run `29490407162` subsequently logged successful Telegram and webhook delivery.

## 4. Fix

For binary strategies, notification detection now prefers a boolean
`position.in_position` transition:

- `false -> true` produces a BUY notification.
- `true -> false` produces a SELL notification.
- If either snapshot lacks the position contract, the existing action-change
  comparison remains as a compatibility fallback.

Continuous-exposure strategies retain their existing exposure-aware path. An
inferred binary event records its detection source and observation timestamp;
it does not claim that the latest snapshot price was the execution price.

## 5. Verification

- RED: three focused tests failed for the missed entry, missed exit, and absent
  previous-position extraction.
- Fresh-context RED: a stable boolean position with a transient `action=buy`
  initially produced a false positive; the compatibility fallback is now
  restricted to snapshots that lack the boolean position contract.
- GREEN: `python -m pytest tests/test_notify.py -q` — 22 passed.
- Regression: `python -m pytest tests/ -q` — 251 passed.
- Historical replay: the actual July 9/10 snapshots produce one BUY note and
  the July 13/14 snapshots produce one SELL note for TrendLock 40 Plus.
- No test contacted Telegram or a webhook endpoint.
