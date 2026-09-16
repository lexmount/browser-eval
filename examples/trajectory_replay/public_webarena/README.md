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
then exposes a harmless readiness page on its unique slice domain. The unauthenticated
control endpoints themselves are never published through the gateway.
`deploy/webarena-rainbond/initializer.sh` is mounted as a Rainbond `config-file` at
`/opt/webarena/initializer.sh` and run as `/bin/sh /opt/webarena/initializer.sh` by
the pinned Alpine image; port 8080 serves only the final readiness marker. Keeping the
script in a mounted file avoids Rainbond's whitespace-only image-command parsing.

Both Magento baseline components must also mount a Rainbond `config-file` named
`wa-hide-csp-*` at `/etc/nginx/conf.d/00-webarena-hide-csp.conf` before they are copied.
Its complete content is:

```text
fastcgi_hide_header Content-Security-Policy-Report-Only;
```

The Nginx configuration changes only the report-only response header. That header
exceeds the upstream-header limit of the APISIX gateway used by this Rainbond
installation and otherwise produces a gateway 502 even while the Pod and port are
healthy. The images retain their default startup commands, and application readiness
still requires a successful request through each public slice domain.

Copy `rainbond-pool.example.json`, set `RAINBOND_API_TOKEN` in the process environment,
and reconcile the warm pool to the active task count plus at least one idle slice.
Additional slices are created in bounded parallel and removed when demand falls:

```text
uv run --frozen python scripts/manage_webarena_rainbond_pool.py \
  --config <pool-config> scale --pool-id <pool-id> --active <N> \
  --minimum-ready 1 --output <runtime-dir>
```

Admission is an atomic lease, so two schedulers cannot assign the same writable sites:

```text
uv run --frozen python scripts/manage_webarena_rainbond_pool.py \
  --config <pool-config> acquire --output <runtime-dir> --owner <task-id>
```

Use the returned config for exactly one task. Releasing a lease creates and verifies a
clean replacement application before deleting the dirty generation and making the slot
available again. If replacement fails, the lease remains held and cannot be reused:

```text
uv run --frozen python scripts/manage_webarena_rainbond_pool.py \
  --config <pool-config> release --output <runtime-dir> --lease-id <lease-id>
```

For a one-shot pool that will be destroyed in full, use:

```text
uv run --frozen python scripts/manage_webarena_rainbond_pool.py \
  --config <pool-config> provision --pool-id <run-id> --count <N> --output <runtime-dir>
```

Pass each leased `webarena.config.json` as a separate `--webarena-config` argument to
`scripts/run_webarena_qualification.py`. Overlapping site batches run concurrently only
when distinct slice configs are supplied. Afterwards, release each reusable lease. For a
one-shot pool, destroy the slices instead:

```text
uv run --frozen python scripts/manage_webarena_rainbond_pool.py \
  --config <pool-config> destroy --slice-dir <runtime-dir>/slice-1 \
  --slice-dir <runtime-dir>/slice-2
```

Each receipt records create, copy, configure, deploy-request, readiness, and total cold-start
durations. Use those measurements to size the warm pool; task admission should allocate a
ready slice rather than waiting for a cold start.

The baseline resource requests total 10 CPU cores and 20 GiB of memory per slice, plus
100 millicores and 128 MiB for the initializer. Capacity planning must use schedulable
resources, not cluster totals. A pool serving `N` active tasks with one idle warm slice
therefore needs at least `(N + 1) * 10.1` cores and `(N + 1) * 20.125 GiB`, with additional
headroom for Rainbond's rolling-update surge Pods and unrelated workloads. If that headroom
is unavailable, replacements remain pending instead of weakening isolation or silently
sharing a site.

Rainbond remains the runtime data plane and this repository's lease controller remains the
source of truth for lifecycle and reset. Kestra is optional above it for queues, schedules,
timeouts, retries, and guaranteed cleanup; it is not required to allocate a slice and must
not become a runtime dependency. Argo Workflows is only justified when the deployment moves
to direct Kubernetes-native orchestration at substantially larger scale, because using it
for the current pool would duplicate Rainbond application lifecycle management.
