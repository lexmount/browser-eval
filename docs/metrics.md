# Web Fetch metric definitions

The formal definitions rendered in every report originate from
`browser_harness_eval/metrics.py`.

- **Transport success**: the provider completed the request and classified the
  response as successful.
- **Strict content pass**: a prominent marker is present, output reaches the
  case-specific minimum length, every frozen fact group has at least one
  accepted alternative, and no hard error/challenge marker is present. Its
  denominator includes transport failures.
- **Fact coverage**: matched ground-truth fact groups divided by all required
  groups. Alternatives are equivalent representations of one semantic fact.
- **Marker recall**: matched markers divided by all registered markers. This is
  a deterministic coverage signal, not a semantic quality score.
- **P50/P95**: client wall-clock latency over transport-success attempts.
- **All-attempts P95**: latency including failures, retained to expose slow
  timeouts and expensive failure paths.
- **Deadline overrun**: client wall-clock duration exceeded the configured
  timeout. This detects libraries whose socket timeout is not a hard end-to-end
  deadline.
- **Structure counts**: Markdown headings, Markdown links and residual HTML
  tags. These diagnose output shape but are not independently quality scores.
- **Artifact integrity**: each output records SHA-256 and unique-line ratio.
  The HTML preview may be truncated, but the complete artifact is copied into
  the portable report and can be checked against the hash.

The answer key is versioned in the dataset and frozen before final scoring.
Supplier outputs do not define ground truth. `rescore` can apply a reviewed
dataset revision to immutable artifacts without making or billing new calls.
An LLM judge, if enabled later, is secondary and does not replace deterministic
facts.

The core dataset intentionally avoids login and anti-bot targets. Unlocking,
geolocation and authenticated browser tasks require separate datasets and must
not be folded into the core success rate.
