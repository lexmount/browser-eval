# Presentation quality

Use this reference when changing a benchmark report's comparison tables, visual emphasis, navigation, responsive layout, or result drill-down.

## Comparison semantics

Every displayed metric must declare one interpretation in the report model:

- **higher is better** when a larger value directly improves the stated decision;
- **lower is better** when a smaller value directly improves it;
- **observational** when direction depends on context or another metric.

Highlight a unique winner only for metrics with a declared direction. Ties remain neutral. Observational metrics must be visibly identified rather than silently left unstyled. Color cannot be the only explanation: provide a short legend or row label that tells the reader why emphasis is present or absent.

For ratios, name numerator and denominator, state whether ratios are computed per matched item or from aggregates, and explain the meaning of values below, equal to, and above 1×. Keep absolute values and ratios visually distinct. Do not infer a winner from a ratio for an observational metric.

Encode these rules once in metric metadata and render from that metadata. Add a cheap release check for missing interpretations, accidental winner styling on observational metrics, incomplete ratio modules, and navigation drift.

## Information architecture

Use short, stable, reader-facing section names. A page should have one primary question and avoid repeating another page's full tables. Put headline decisions and the minimum evidence needed to understand them on the first page; put task-level comparison, failure analysis, and evidence drill-down together when readers move among them.

Treat report structure as an information architecture, not a running list of requested explanations. Assign each fact to exactly one primary location: headline comparison, dataset definition, methodology, filterable result inventory, or result detail. Link to that owner instead of adding another summary table. If a process has at least three dependent stages or a meaningful branch, lead with a Mermaid or ImageGen diagram; use adjacent prose only for definitions, constraints, exceptions, and evidence that the diagram cannot carry.

Result states must link to the corresponding task and method evidence. Filters should preserve all compared methods in the result row and support more than two methods when the evaluation contract permits it.

## Proportional checks

Choose checks from the changed surface:

| Change | Required check |
| --- | --- |
| Metric value or comparison logic | Unit test the semantic contract and independently recompute affected derived values |
| Table markup or emphasis | Render representative directional, tied, missing, and observational rows |
| Navigation or drill-down | Deterministically check all routes and links; render every top-level page once |
| CSS, layout, or responsive behavior | Run shared component coverage; visually inspect only the affected page types at one representative width |
| Text-only wording | Factual and terminology review; no visual sweep unless wrapping or hierarchy changes |
| Release metadata only | Binding and destination check only |

Automate deterministic invariants in code. Keep visual judgment conditional on relevant layout or style changes. A release gate should fail with the missing invariant, not merely recommend that someone “review the report.”
