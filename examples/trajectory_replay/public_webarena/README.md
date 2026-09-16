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
The initializer depends on the copied Reddit and GitLab components so Rainbond makes
their internal Service names resolvable. Keep only the site-specific exported variables
`REDDIT_CTRL_URL` and `GITLAB_CTRL_URL`; remove generic duplicated `CTRL_HOST` and
`CTRL_PORT` exports before creating both dependency relations.

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

The GitLab baseline must reproduce the upstream Compose `shm_size: 2gb` contract with
native Kubernetes attributes, not a Rainbond `memoryfs` storage volume. In the GitLab
component's **Other settings → Kubernetes attributes**, set `volumes` to:

```yaml
- name: gitlab-shm
  emptyDir:
    medium: Memory
    sizeLimit: 2Gi
```

and set `volumeMounts` to:

```yaml
- name: gitlab-shm
  mountPath: /dev/shm
```

Rainbond attribute values omit the top-level `volumes:` and `volumeMounts:` keys.
Verify inside a running GitLab container that `/dev/shm` is a 2 GiB `tmpfs`; a
Rainbond `memoryfs` volume is backed by the cluster filesystem in this deployment and
is not equivalent. The four seeded site images otherwise keep their data in the
slice's disposable writable layers. Do not mount empty volumes over their preloaded
database directories.

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
available again. If replacement fails, the lease remains held, the dirty generation is
stopped, and the slot cannot be reused:

```text
uv run --frozen python scripts/manage_webarena_rainbond_pool.py \
  --config <pool-config> release --output <runtime-dir> --lease-id <lease-id>
```

The default `replace_before_destroy` strategy allows the dirty and replacement generations
to overlap while the replacement becomes ready. This preserves available capacity during
normal concurrent operation without imposing a fixed pool size. `stop_before_replace` is
an operator-selected fallback for a temporarily constrained cluster: it stops the dirty
generation before creating its replacement. Either way, a failed reset leaves the lease
held and the dirty generation stopped rather than returning an invalid slot to the pool.

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

After a slice becomes ready, exercise all four public origins concurrently:

```text
WEBARENA_RAINBOND_SMOKE_URLS='{
  "shopping_admin":"http://<shopping-admin-domain>",
  "shopping":"http://<shopping-domain>",
  "reddit":"http://<reddit-domain>",
  "gitlab":"http://<gitlab-domain>"
}' uv run --frozen python -m pytest \
  tests/trajectory_replay/public_webarena/test_rainbond_live_smoke.py \
  -m real_smoke -q -s
```

The smoke test makes eight simultaneous page loads per site, checks that each origin
returns its expected application, and rejects a per-site P95 above ten seconds.

The baseline resource requests total 10 CPU cores and 20 GiB of memory per slice, plus
100 millicores and 128 MiB for the initializer. Capacity planning must use schedulable
resources, not cluster totals. Reconcile the pool to current active demand plus the chosen
idle reserve; the controller destroys excess unleased slices when demand falls. Rainbond
may also need temporary headroom while a replacement generation starts and for rolling
updates. When that headroom is unavailable, use `stop_before_replace` for that deployment
rather than weakening isolation or sharing writable sites.

Rainbond is the runtime data plane and this repository's lease controller is the source of
truth for allocation, reset, quarantine, and cleanup.
