# Headless comparison: private editorial and measurement audit

This file is not a report page and must not be copied to the publication bundle.
Publication remains blocked until every new exact-body lifecycle is verified.

## Question and evidence ledger

| Claim / decision | Evidence and calculation | Limit / competing explanation |
|---|---|---|
| Compare delivery before interpreting speed | Existing 150 case definitions; `score_attempt`, `task_passed`; `headless_evidence` computes per-family counts | Six families have different output contracts; no combined browser score |
| Compare latency on identical passed URLs | `pairs[].both_pass_ids`; median of each provider's elapsed time on precisely that set | Each pair has its own denominator; one call per URL; network and machine contention not isolated |
| Whole-HTML conversion can dominate this path | `english-encyclopedia`, Moli/Lightpanda/Chromium phase metadata; fetch 1869.41/2475.25/2853.32 ms, conversion 40633.69/40662.87/40803.63 ms | A real three-call example, not attribution for all failures. Obscura/Kitesurf timeout phase breakdown unavailable |
| Lifecycle event is not business completion | `dynamic-delayed`: requires 10 initial quotes; `dynamic-iframe`: requires 14 names in child document; worker uses DOMContentLoaded plus top-level outerHTML | Does not prove absence of JavaScript support; wait semantics and additional engine-internal settling can differ |
| Dynamic-task ranking differs from ordinary-web ranking | Final dynamic passes: Kitesurf 6/10, Obscura 5/10, Chromium/Lightpanda 1/10, Moli 0/10. Inspected `dynamic-products-api` Moli and Kitesurf stored Markdown: empty cards/templates versus actual products | Both outputs contain the same "Enable JavaScript" prompt, so that phrase alone is not evidence of JS being disabled. Waiting, settling and script compatibility were not isolated |
| Same format does not mean same body scope | Worker converts entire returned DOM; retained raw HTML and Markdown; word P/R/F1 separately computed | Noise may include useful out-of-reference material; overlap is not factual correctness |
| Fallback choice depends on unique passes | `pairs[].provider_only`, `chromium_only`, union | Offline set union is not an executed fallback service, trigger quality or expected production latency |
| File contract is separate from HTML rendering | CLI non-UTF-8 output retained as binary; document/image/video task rules unchanged | Current adapter does not enable product-native file parsing, media understanding or downloads; cannot infer those capabilities from failures |

## Corrections and rejected evidence

- Initial diagnostic runs in `runs/headless-web` and `runs/headless-nonweb`
  suffered GIL blocking from quadratic whitespace normalization. They were
  stopped and labelled invalid timing measurements. The final run began after
  an equivalent linear regex correction, tested across 16,807 short inputs and
  large whitespace runs. No score threshold was changed.
- Pre-call references were independently frozen. Five changed ordinary-page
  bodies were refreshed from independent source captures, never provider output.
  Stable contracts were not refreshed to current ranking names or prices.
- The live final run was partitioned by completed matched case for prompt
  postcheck certification, retaining its actual preflight bindings and calls.
- Three first-pass projections (`live-060-database`, `live-061-version-control`,
  `live-062-natural-language-processing`) failed on assembly end bounds. The
  runner starts its monotonic clock before recording wall-clock start, so
  start+elapsed can slightly exceed the recorded wall-clock finish. No call
  timestamps or failed certificates were edited. Three fresh five-provider
  cohorts were independently prefrozen, executed and postchecked successfully;
  replacement choice was caused by certificate failure, not scores.
- `web-retail-returns` precheck observed the complete article; postcheck hit a
  human-verification page. Its reference failure is not a provider failure. Keep
  the original output and scoring diagnostics, do not certify by repeated
  postcheck attempts, and require a fresh complete cohort after independent
  reference access is restored.

## Editorial / Humanizer passes

The drafted article is an argument about the HTML delivery chain, not a tour of
dashboard tabs. It uses the actual encyclopedia call to distinguish fetch from
conversion, then connects the same extraction boundary to dynamic and file
tasks. All six task families are represented. Matched-task latency and unique
pass sets provide decision-relevant analysis beyond ranking totals.

Factual pass: no CPU/RSS or cost-efficiency claim; Kitesurf public remote
deployment is named where latency is interpreted; no general engine-JS claim
from missing delayed records; no unmeasured timeout phase attribution; no
equating structure with truth or freshness. Unknown body/title evidence remains
unmeasured. Task outcomes and overlap remain independent.

Humanizer pass: removed promotional framing and generic superiority claims;
kept concrete numbers with denominators and a concrete mechanism; no author
anecdotes, staged questions, invented quotations or instruction chatter in the
authored body. Necessary boundary explanations remain beside the claims they
limit. The ending identifies three different implementation decisions rather
than announcing a universal winner.

The online and standalone presentations share one authored template and inline
standalone CSS. Final data-bound review, visual desktop/narrow/print QA and
publication sign-off must wait for complete verified evidence. Unit tests alone
do not certify editorial or visual quality.
