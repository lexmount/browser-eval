# WebArena fixed-trajectory replay

This is a deterministic replay of qualified public WebArena human recordings. The runner
executes browser UI actions without an agent or model and checks the final answer, service
state, and HAR with WebArena-Verified. It does not score agent planning.

The source inventory contains 179 candidate recordings. The current formal suite marks
30 as eligible; the runner skips the other 149. Source actions are candidates, not a
passing answer key: qualification uses independent Chromium runs and the official
evaluator before a task enters the formal suite.

The repository-curated 30 browser-tool cases use
[`examples/trajectory_replay`](../README.md) and `harness.trajectory_replay`.
The autonomous Agent evaluation uses `harness.browser_use`. Neither shares this
public suite's denominator or result pages.

To reproduce the public suite on a prepared WebArena-Verified environment, follow the
[two-repository contract](../../../docs/webarena-fixed-trajectory-reproduction.md) and the
[trajectory protocol](../../../docs/trajectory-replay-protocol.md). The formal entry point is:

```text
uv run --frozen python -m harness.trajectory_replay.public_webarena.cli \
  --suite evaluation/browser-use-public/webarena-human-suite.json \
  --webarena-config <runtime-config> --env-file .env \
  --browser-docker-host unix:///var/run/docker.sock \
  --engines chromium,moli --failure-retries 1 \
  --output <evidence>/formal/webarena-public-trajectories/run \
  --reports <evidence>/formal/webarena-public-trajectories/reports
```

Measured attempts, screenshots, HAR, and reports stay outside Git. The repository keeps
the suite, qualification reasons, evaluator rules, source bindings, and a sanitized
publication summary. Use `verify_metrics.py` to audit raw measurements and
`rebuild_report.py` to render from existing attempts; neither reruns browsers.
