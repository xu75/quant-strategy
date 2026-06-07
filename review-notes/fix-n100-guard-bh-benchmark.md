---
review_target_id: fix-n100-guard-bh-benchmark
branch: fix/n100-guard-bh-benchmark
author: "[砚砚/GPT-5.3-Codex🐾]"
created: 2026-06-06
---

# Review Request: N100 Guard-Z B&H Benchmark Fix

## Original Requirement

Source: current thread, 2026-06-06.

> https://quant-strategy.mesh-hub.xyz/backtest/n100-guard-z 这个结果明显有问题，你检查一下

## What

- Fix N100 Guard-Z B&H benchmark semantics so full and period metrics use split-adjusted `513100`, not raw 513100 or primary QQQ.
- Recompute A-share ETF `adj_close` with date-specific `cum_nav / nav` factors, including split-factor alignment when NAV factor changes before the exchange price jump.
- Recompute N100 Guard-Z backtest data and charts.
- Fix Astro drawdown rendering so negative stored values do not display as `--30.18%`.

## Why

The live page showed N100 Guard-Z full B&H as `+127.54%` with B&H max drawdown `--85.5%`. The raw 513100 close series contained fund split/unit conversion jumps, so B&H was dramatically understated and drawdown overstated. Period summaries also used QQQ as the B&H path before this change.

## Tradeoff

The ETF CSV diffs are large for 513100/159941 because historical `adj_close` is corrected across split regimes. Strategy equity remains unchanged, but reported trade entry/exit display prices now use the corrected historical adjusted scale rather than a terminal constant multiplier.

## Open Questions For Review

- Is `_align_split_factor_to_price_jumps` conservative enough for future ETF split timing edge cases?
- Should the A-share updater persist NAV split factor diagnostics for auditability?
- Is it acceptable that generated `latest.json` only changes timestamp and a floating-point tail after the full backtest rerun?

## Evidence

- `.venv/bin/python -m pytest` -> 183 passed, 4 existing warnings.
- `pnpm build` in `site/` -> Astro build succeeded, 22 pages built.
- Local preview `http://127.0.0.1:3005/backtest/n100-guard-z` snapshot showed `+1034.75%`, `-30.18%`, `-28.57%`.
- ETF `adj_close` scan: all A-share ETF max one-day moves are now about +/-10%; no 80% split artifacts remain.
