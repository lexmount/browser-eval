# Metrics

- Request success: normal provider responses divided by all calls. It does not prove task success.
- Task pass: completed task contracts divided by all tasks, including request failures. This is the default ranking metric.
- Body tasks require the length, fact and content rules to pass. Complete references additionally support recall (completeness), precision (purity), and harmonic-mean F1. Failed calls score zero; missing references are not zero scores.
- Resource tasks match all required resource addresses. Echoing a direct-file input URL is not file retrieval.
- File tasks require returned bytes or a controlled attachment verified against the source file. A URL or misdecoded binary string does not pass.
- Dynamic lists require at least three distinct named in-domain detail entries. Names, rank, prices and IDs are not fixed. This measures structure, not factual correctness or full-list completeness.
- Ranking mean/P50/P95 include only task-passing calls. Individual records show the elapsed time even for failures.
- Failure classes summarize request errors or the missing task requirement. Detailed records retain the full failure information.
- Every persisted output has a SHA-256 check. A report-only change must not alter outputs or silently change scores.

Labels and explanations are centralized in `report_components.py` and appear above the corresponding tables. Conditional-on-request-success pass rates are not shown in the report.
