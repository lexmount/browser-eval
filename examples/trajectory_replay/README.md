# WebArena fixed-trajectory replay

This is a deterministic replay of qualified public WebArena human recordings. The runner
executes browser UI actions without an agent or model and checks the final answer, service
state, and HAR with WebArena-Verified. It does not score agent planning.

The source inventory contains 179 candidate recordings. The current formal suite marks
30 as eligible; the runner skips the other 149. Source actions are candidates, not a
passing answer key: qualification uses independent Chromium runs and the official
evaluator before a task enters the formal suite.

The original repository-curated 30 browser-tool cases remain in
[`examples/browser_use`](../browser_use/README.md) and run through
`harness.browser_use`. Their tasks, results, and report are separate from this suite.

To reproduce the public suite on a prepared WebArena-Verified environment, follow the
[two-repository contract](../../docs/webarena-fixed-trajectory-reproduction.md) and the
[trajectory protocol](../../docs/trajectory-replay-protocol.md). The formal entry point is:

```text
uv run --frozen python -m harness.trajectory_replay.cli \
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
