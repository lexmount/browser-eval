# Web Fetch metric definitions

The formal definitions rendered in every report originate from
`browser_harness_eval/metrics.py`.

- **Transport success**: the provider completed the request and classified the
  response as successful.
- **Content pass**: at least one case-specific marker appears in persisted
  output. Its denominator includes transport failures.
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

The core dataset intentionally avoids login and anti-bot targets. Unlocking,
geolocation and authenticated browser tasks require separate datasets and must
not be folded into the core success rate.
