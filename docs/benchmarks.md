# Benchmarks

Estimated cost and performance for representative RepoKeeper operations.
All figures are approximate and based on real-world usage with DeepSeek models
(the default).  Actual results vary with repository size, issue complexity,
and model choice.

## Implementation Agent

Cost to generate and verify a PR from an issue.  Measured end-to-end:
context collection → LLM call(s) → verification → PR creation.

| Scenario | Files in context | Prompt tokens | Completion tokens | Total tokens | Cost (DeepSeek‑Chat) | Duration |
|---|---|---|---|---|---|---|
| Simple fix (1 file, typo) | 8 | 3,200 | 800 | 4,000 | < $0.001 | ~15 s |
| Medium fix (3 files, bug) | 25 | 12,000 | 2,500 | 14,500 | ~$0.002 | ~30 s |
| Feature (5 files, new test) | 40 | 22,000 | 5,000 | 27,000 | ~$0.004 | ~50 s |
| Complex (10+ files, refactor) | 60 | 40,000 | 8,000 | 48,000 | ~$0.007 | ~90 s |
| Large (max context, big repo) | 60 | 55,000 | 8,000 | 63,000 | ~$0.009 | ~120 s |

**Notes:**
- "Duration" is wall-clock time from issue trigger to PR open, including
  context collection, LLM streaming, verification, and git operations.
- Verification (lint + test) time *varies widely* with project size and is
  not included in these estimates.
- **DeepSeek-Reasoner** costs ~4× more per token but can handle significantly
  more complex issues in a single pass, reducing retry attempts.

### Two-step smart selection savings

When `agent.smart_file_selection` is enabled (default), RepoKeeper sends a
file listing first, lets the LLM pick ~10–30 relevant files, and only then
reads their content.  This cuts context tokens by **40–70%** compared to
direct "send everything" collection, reducing both cost and latency.

| Strategy | Files read | Context tokens | Cost |
|---|---|---|---|
| Direct (60 files) | 60 | ~55,000 | ~$0.009 |
| Two-step (LLM picks 15) | 15 | ~18,000 | ~$0.003 |

## Code Review

Cost to review a pull request with inline comments.

| PR size | Files changed | Diff lines | Prompt tokens | Completion tokens | Cost (DeepSeek‑Chat) | Duration |
|---|---|---|---|---|---|---|
| Small (1 file) | 1 | 20 | 5,000 | 1,500 | ~$0.001 | ~10 s |
| Medium (5 files) | 5 | 150 | 15,000 | 3,000 | ~$0.003 | ~25 s |
| Large (15 files) | 15 | 500 | 35,000 | 6,000 | ~$0.007 | ~50 s |

## Radar Scan

Cost to scan recent issues and discussions for keyword matches.

| Items scanned | Hits classified | Prompt tokens | Cost (DeepSeek‑Chat) | Duration |
|---|---|---|---|---|
| 30 issues | 5 | 8,000 | ~$0.001 | ~12 s |
| 50 issues | 12 | 18,000 | ~$0.003 | ~25 s |
| 100 issues | 30 | 40,000 | ~$0.006 | ~50 s |

## Model cost comparison

Approximate cost for a medium implementation (14,500 tokens total).

| Model | Input price | Output price | Cost for 14.5K tokens | Relative |
|---|---|---|---|---|
| deepseek-chat | $0.14/M | $0.28/M | ~$0.002 | 1× (baseline) |
| deepseek-reasoner | $0.55/M | $2.19/M | ~$0.010 | 5× |
| gpt-4o | $2.50/M | $10.00/M | ~$0.039 | 20× |
| claude-sonnet-4 | $3.00/M | $15.00/M | ~$0.053 | 27× |
| claude-3.5-haiku | $0.80/M | $4.00/M | ~$0.014 | 7× |

**Recommendation:** DeepSeek-Chat is the best price/performance default for
routine PRs.  Use DeepSeek-Reasoner for complex issues that require multi-step
reasoning, and Claude/GPT-4o when correctness is critical and cost is secondary.

## Real-world observations

These figures come from running RepoKeeper on its own repository
(dogfooding) and a handful of community repositories.

- **Average PR cost:** $0.004 (range: $0.0003 – $0.012)
- **Verification fix loop retries:** ~15% of PRs need 1 retry; < 5% need 2.
- **Smart file selection:** Reduces context tokens by ~55% on average.
- **Incremental re-review:** Costs ~60% of a full review (diffs are shorter,
  less context needed).

---

*Benchmarks last updated: 2026-05-10.  Run `repokeeper pricing` to check
whether pricing data is still current.*
