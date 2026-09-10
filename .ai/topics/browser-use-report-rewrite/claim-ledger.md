# Browser Use report claim ledger

This file is an editorial input, not part of the published report bundle.

| Claim | Status | Evidence | Boundary |
|---|---|---|---|
| Chromium and Moli each passed 25/30 primary attempts. | measured | `attempts.jsonl`, primary attempts only | Applies to this 30-task cohort and fixed run configuration. |
| There were zero discordant primary pairs. | derived | paired join on task ID and engine | Does not establish browser equivalence. |
| The five primary failures were shared: 68, 124, 142, 168, 506. | derived | primary attempt score pairs | Shared variables include agent, model, prompt, step limit, and evaluator interface. |
| Ten permitted failure retries produced zero recoveries. | measured | attempt 2 records | Retry results are diagnostic and never replace pass@1. |
| All 70 recorded attempts passed deterministic evidence review. | measured | `result-review.json` | Review checks integrity and agreement; it is not a second evaluator. |
| WebArena-Verified is the best first benchmark for this browser A/B. | interpretation | deterministic result evaluation, resettable self-hosted sites | It is operationally heavy and does not cover open-web drift. |
| Next expansion should seek kernel-sensitive, deterministically verifiable tasks. | proposal | zero discordance plus shared failure pattern | Must remain a new pre-registered run; do not alter this cohort. |
