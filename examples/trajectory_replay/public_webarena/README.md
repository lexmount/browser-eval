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

## Disposable Rainbond environments

For concurrent state-changing runs, use a complete Rainbond slice per worker instead of
sharing sites or relying on separate login accounts. A slice contains Shopping Admin,
Shopping, Reddit, and GitLab with independent writable container layers and unique
domains. Reset deletes and recreates the whole slice from the pinned baseline; destroy
removes the application. The read-only Map service may be shared.

The baseline also contains a small digest-pinned initializer component. It calls the
Reddit and GitLab `env-ctrl` endpoints only over Rainbond's internal service network,
then exposes a harmless readiness page. The unauthenticated control endpoints are never
published through the gateway. Its command is the base64-encoded content of
`deploy/webarena-rainbond/initializer.sh`, executed by the pinned Alpine image; port
8080 is internal-only and is the component readiness probe.

Copy `rainbond-pool.example.json`, set `RAINBOND_API_TOKEN` in the process environment,
and reconcile the warm pool to the required concurrency. Keep at least one ready slice;
additional slices are created in bounded parallel and removed when demand falls:

```text
uv run --frozen python scripts/manage_webarena_rainbond_pool.py \
  --config <pool-config> scale --pool-id <pool-id> --count <N> \
  --minimum-ready 1 --output <runtime-dir>
```

For a one-shot pool that will be destroyed in full, use:

```text
uv run --frozen python scripts/manage_webarena_rainbond_pool.py \
  --config <pool-config> provision --pool-id <run-id> --count <N> --output <runtime-dir>
```

Pass each generated `slice-*/webarena.config.json` as a separate
`--webarena-config` argument to `scripts/run_webarena_qualification.py`. Overlapping
site batches run concurrently only when distinct slice configs are supplied. Afterwards:

```text
uv run --frozen python scripts/manage_webarena_rainbond_pool.py \
  --config <pool-config> destroy --slice-dir <runtime-dir>/slice-1 \
  --slice-dir <runtime-dir>/slice-2
```

Each receipt records create, copy, configure, deploy-request, readiness, and total cold-start
durations. Use those measurements to size the warm pool; task admission should allocate a
ready slice rather than waiting for a cold start.
