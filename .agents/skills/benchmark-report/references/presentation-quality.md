# Presentation quality

Use this reference when authoring a report page or changing a report's comparison tables, visual emphasis, navigation, responsive layout, or result drill-down. These rules apply to every report track; track guides add evidence types.

## Reader-facing structure

Make the home and interpretation routes presentation-ready. Each section should make one finding legible through a data-bearing chart, mechanism diagram, meaningful image, or inspectable specimen, followed by short explanatory prose. Keep exact values in accessible text. Decoration is not a visual explanation: remove images that carry no data, mechanism, topology, or evidence. Dense sortable inventories and raw logs remain on the analysis and evidence routes that own them.

When multiple methods have material failures, give each one a separate interpretation chapter containing its full reviewed subtype distribution, a plain-language explanation of the dominant mechanisms, representative result evidence, and the difference from the other methods. The home page should show only a compact leading-error signature; result analysis owns complete stage and case distributions.

After the factual pass, run Humanizer over finished report-authored headings, prose and captions and over articles, and also Humanizer Chinese on Chinese text. Do not apply them to catalog interface strings, quotations or measured text. These passes may improve language only; they must preserve numbers, scope, technical meaning, and uncertainty.

Organize around reader questions, not storage tables. A useful report usually makes these answers easy to find:

- What was compared, on which tasks and versions?
- What is the headline result, with denominator and uncertainty?
- Why do methods differ, and which differences were actually measured?
- What does one representative task require, return, and score?
- Where did methods fail, and is the cause provider, source, evaluator, or environment?
- How were all results independently checked, what changed, and what remains unresolved?
- What is needed to reproduce the current run?

A report interpretation page must stand alone as the reader's main explanation: lead with the measured outcome and denominator, show a compact method comparison, explain the scoring mechanism with at least one inspectable source/reference/output example, diagnose each method's material issue, distinguish measurement from causation, and end with supported conclusions and review scope. If a reference report is supplied, match its reading sequence and evidence depth, not its facts. A renderer check should validate those facts, values, evidence targets and nearby caveats from the current measured model, with negative controls that reject a missing method, broken example, misplaced timing context, or empty conclusion; rules in Markdown alone are insufficient. A visual reader pass remains necessary because structural checks cannot judge whether prose is clear.

Show the task ID and run ID on result pages. Put the task requirement, usable reference scope, complete actual output, normalization, individual checks, and verdict rationale together. If the reference is shorter than the output or only covers anchors, state locally that the metric measures required coverage rather than whole-output equality.

Keep environment and reproduction together and deduplicated. Show repository/version, platform, method versions and routes, material runtime settings, credential names and acquisition locations, and current commands. Never expose secret values or machine-local paths.

Use reader-facing labels throughout reports. Do not publish unexplained internal terms or shorthand; Chinese copy must be understandable to a general reader without benchmark implementation context. State the population, denominator, and outcome relation where a compact label would otherwise be ambiguous.

English routes may preserve measured task names, IDs and evidence verbatim, but all report-authored interface and explanatory prose must be localized. Release checks must reject known untranslated interface markers and untranslated accessibility attributes.

## Comparison semantics

Every displayed metric must declare one interpretation in the report model:

- **higher is better** when a larger value directly improves the stated decision;
- **lower is better** when a smaller value directly improves it;
- **observational** when direction depends on context or another metric.

Highlight extrema only for metrics with a declared direction. Web Extract highlights every tied best value without extra tie labels; other tracks highlight unique extrema. Unavailable values stay neutral. Observational metrics must be visibly identified rather than silently left unstyled. Color cannot be the only explanation: provide a short legend or row label that tells the reader why emphasis is present or absent.

For ratios, name numerator and denominator, state whether ratios are computed per matched item or from aggregates, and explain the meaning of values below, equal to, and above 1×. Keep absolute values and ratios visually distinct. Do not infer a winner from a ratio for an observational metric.

Put a core-metrics comparison table near the start of every report home page, before secondary diagnosis or interpretation. Prefer tables over repeated cards for dense categorical summaries. Generate its rows and columns from the participant registry rather than assuming a fixed participant count.

When a current report has CPU, memory or network evidence, show the same grouped resource comparison on the home page and interpretation page. Lead with the evidence-derived winner for every metric where lower consumption has a clear meaning, mark winning cells in the tables, and state that resource leadership is separate from task pass rate. Do not label utilization or transfer rate as "better" merely because it is lower. Release checks must fail if the winner summary, grouped tables or winner markers are missing in any generated language.

Encode these rules once in metric metadata and render from that metadata. Add a cheap release check for missing interpretations, accidental winner styling on observational metrics, incomplete ratio modules, and navigation drift.

## Information architecture

Use short, stable, reader-facing section names. A page should have one primary question and avoid repeating another page's full tables. Put headline decisions and the minimum evidence needed to understand them on the first page; put task-level comparison, failure analysis, and evidence drill-down together when readers move among them.

Treat report structure as an information architecture, not a running list of requested explanations. Assign each fact to exactly one primary location: headline comparison, dataset definition, methodology, filterable result inventory, or result detail. Link to that owner instead of adding another summary table. If a process has at least three dependent stages or a meaningful branch, lead with a Mermaid diagram or a generated image that encodes the flow; use adjacent prose only for definitions, constraints, exceptions, and evidence that the diagram cannot carry.

Before adding a section, first place the information in the existing summary, filterable inventory, or result detail that owns it; remove duplicated tables and prose.

Keep pass, fail and all result filters with failure classification, defaulting to fail; a track may fall back to all when nothing failed. Result inventories are fixed-height, horizontally scrollable diagnosis tables. Do not place full reviewed causes, evaluator payloads, latency stacks or other paragraph-length content in inventory cells. Every failure row opens an in-page diagnosis with the failure stage, exact program error, reviewed cause, treatment and links to the relevant trajectory, screenshots and logs; the full result page remains the canonical evidence view.

Result states must link to the corresponding task and method evidence. Filters should preserve all compared methods in the result row and support more than two methods when the evaluation contract permits it.

## Proportional checks

Choose checks from the changed surface:

| Change | Required check |
| --- | --- |
| Metric value or comparison logic | Unit test the semantic contract and independently recompute affected derived values |
| Table markup or emphasis | Render representative directional, tied, missing, and observational rows |
| Navigation or drill-down | Deterministically check all routes and links; render every top-level page once |
| CSS, layout, or responsive behavior | Run shared component coverage; visually inspect the affected page types at one representative width, plus the release reader pass when publishing |
| Text-only wording | Factual and terminology review; no visual sweep unless wrapping or hierarchy changes |
| Release metadata only | Binding and destination check only |

Automate deterministic invariants in code. Keep visual judgment conditional on relevant layout or style changes. A release gate should fail with the missing invariant, not merely recommend that someone “review the report.”
