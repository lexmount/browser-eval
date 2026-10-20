# Read Web Fetch results

A successful request means the method returned a normal response. A task passes only when that response meets the task's requirements. The default ranking uses task pass rate, with failed requests included.

## What counts as a pass?

| Task | Required result |
| --- | --- |
| Body | Text meeting the declared length, fact and content rules |
| Structure | The required fields, record types and unique entries; dynamic lists need at least three distinct named in-domain detail entries |
| Resource | All required resource addresses |
| File | Actual bytes or a controlled attachment verified against the source file |

A returned file URL is not a delivered file. Dynamic list checks measure structure; they do not prove factual accuracy or complete list coverage. Names, ranks, prices and IDs are not frozen for those checks.

## Quality and timing

| Metric | How to read it |
| --- | --- |
| Body recall | How much of a complete reference was returned |
| Body precision | How much returned text belongs to that reference |
| Body F1 | Harmonic mean of precision and recall |
| Mean / P50 / P95 latency | In the default ranking, timing of task-passing calls only; failures still retain elapsed time in their records |
| All-call mean / P50 / P95 | The complete call population, including failed calls and timeouts; exposed as `mean_all_attempts_ms`, `all_attempts_p50_ms` and `all_attempts_p95_ms` in scorer summaries |
| Failure class | Request error or unmet task requirement; open the detail for the full error |

Body metrics require a complete, comparable reference. A failed call scores zero; a missing reference leaves the metric unavailable. Check each report's stated timing population before comparing it with another report.

Outputs have SHA-256 hashes. Rebuilding or editing a report must preserve the saved responses and scores. Metric labels come from `report_components.py`; the report does not show a pass rate conditioned only on successful requests.

Formal cohorts reject selected tasks whose acceptance rules are unresolved before capturing references or calling providers. A published method must use one executable fingerprint across its selected results. Compare different builds as separate complete method populations. Offline file rescoring reads the retained file bytes and applies the current file requirements while preserving the original call metadata.
