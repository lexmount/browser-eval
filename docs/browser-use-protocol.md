# Browser-use reference-trajectory protocol

This protocol keeps task data, browser actions, evidence, and scoring identical across browser
engines while allowing datasets to provide different task-specific oracles.

## Common layers

Every suite is normalized to `lex_browser_use_tasks/4` and every reference path to
`lex_browser_trajectory/1`.

1. The task layer identifies the task, source, environment, and immutable upstream revision.
2. The trajectory layer contains only browser UI operations. It cannot call HTTP APIs, execute
   arbitrary JavaScript, write a database, or read the expected answer.
3. The evidence layer records the executed calls, observed output, URLs, network HAR, timing,
   resources, and termination reason for every attempt.
4. The evaluator layer receives the same evidence envelope. Repository-curated tasks use exact
   output assertions. WebArena tasks call the pinned WebArena-Verified evaluator.

Legacy `lex_browser_use_tasks/3` suites are normalized to these four layers by the loader. Their
existing ground-truth file remains bound to the unchanged v3 dataset hash.

## Targets and actions

Structured targets support CSS, complete Playwright selectors, role and accessible name, label,
visible text, and test ID. A structured target must resolve to exactly one element unless the task
explicitly permits multiple matches. Each action may identify a tab by its zero-based index.

The action allowlist is navigate, click, double click, fill, select, check, uncheck, hover, press,
get text, get attribute, get property, snapshot, history navigation, and scroll. Readiness waits are
part of an action and do not count as additional trajectory steps.

Every declared action has the same 30-second default timeout in both datasets and both engines;
tasks may declare a different timeout in their trajectory. Duration is still measured continuously.
This avoids turning a 5-second application navigation delay into a false trajectory failure.

## WebArena trajectory qualification

The official WebArena resource contains 179 recorded human trajectories and reports an aggregate
human success rate of 78.24%. A recording is therefore a candidate path, not automatically a
successful gold path.

The pinned import contains all 179 recordings and 3,394 normalized browser operations. It has no
unmapped Playwright API calls; 408 manually typed values and three clipboard-pasted values had to
be recovered from trace snapshots because they were not recorded as `fill` calls. Tasks 300 and
431 contain no recorded actions. These are structural-import facts, not replay validation: before
qualification the reliable/eligible count remains zero.

A separate source-recording audit injects each task's official expected response and evaluates its
hash-bound Playwright network trace with WebArena-Verified 1.2.3. It records 92 successes, 86
failures, and one evaluator error. This does not replay the current environment and therefore does
not qualify any task; it proves that the 179 published recordings cannot all be treated as passing
gold paths. The sanitized per-task result is retained in
`evaluation/browser-use-public/webarena-recorded-outcome-audit.json`.

Of the 179 candidates, 98 mutate or navigate tasks already have a null response contract whose
success can be decided from final state and HAR. The other 81 are retrieve tasks: the upstream
recording provides actions but no evaluator-ready final answer. Even if their imported path
executes, they remain `needs response annotation` until Codex adds DOM observation actions and an
observed response builder, then passes a second Chromium qualification run.

All 179 candidates are replayed once with upstream Chromium against the pinned WebArena-Verified
environment before either measured browser is run. Each task enters one of three queues:

- eligible: the path executes and the current deterministic evaluator passes;
- needs response annotation: the UI path executes but a retrieve response still needs an
  independent DOM capture;
- needs trace repair: an action or final-state evaluation fails.

For correction, Codex may inspect the original trace, its snapshots, the current page, and the
failure evidence. It may change UI actions or add DOM capture steps. It may not change the task,
expected result, evaluator, or environment. A repaired task becomes eligible only after a new
Chromium run passes the same WebArena-Verified evaluator. Original and corrected trajectories,
diffs, evidence hashes, and correction reasons are retained. A task that cannot be completed and
independently verified is marked skipped with a public reason.

HAR creator version 3 combines CDP's base request/response events with
`Network.requestWillBeSentExtraInfo` and `Network.responseReceivedExtraInfo`. The extra events are
required because Chromium emits navigation-defining `Accept` and `Sec-Fetch-*` request headers
there rather than in the base request event. Version 3 also preserves every redirect hop: CDP
reuses one request ID across a POST, its redirect response, and the following GET, so retaining
only the last event would silently remove evaluator-relevant state-changing requests. HAR keeps
request methods, URLs, bodies, status codes, and header names needed for evaluator and audit use,
but redacts `Authorization`, proxy
authorization, cookies, `Set-Cookie`, and WebArena
auto-login header values before the file is written. The only cookie values retained are the
evaluator-required non-authentication fields `mage-messages` and `_osm_directions_engine`; all other
cookie values remain redacted. Response bodies whose MIME type the official evaluator cannot parse
(including HTML, scripts, images, and fonts) are omitted. Login preparation uses the official
WebArena form selectors and runs before measured capture. The Verified container images do not
implement the legacy auto-login request headers, so those headers are not part of the runtime
contract.
Repository and published artifacts never contain runtime credentials or session values.
Runtime JSON uses `credentials_env` to name the username and password environment variables; it
does not contain their values. The runner resolves them only in memory and fails before login when
a variable is absent or when both inline credentials and environment references are configured.
The unmodified official evaluator reads the same credential-free JSON for URL rendering.

Before qualification, the optional runtime smoke starts each browser image without a CPU limit,
loads only the selected self-hosted site roots, and writes redacted HAR plus a
`diagnostic_only=true` receipt. It validates CDP and network compatibility but is never eligible as
trajectory qualification or measured browser evidence:

```text
uv run --frozen python -m scripts.smoke_webarena_runtime --config <runtime-config> --output <evidence>/diagnostics/runtime-smoke --site shopping_admin --site shopping --site reddit
```

Add `--verify-login` to exercise the same pre-measurement form-login path. Authentication completes
before HAR capture, so credentials and login request bodies never enter diagnostic evidence.
Add `--cpus 2` to exercise the measured browser daemon's cgroup preflight and quota without turning
the smoke into qualification evidence.

Qualification must not inspect Moli results or the later Chromium-versus-Moli comparison. This
prevents browser-specific post-selection.
Qualification shards are globally sequential within one evidence root. A process lock rejects
parallel site batches because shared CPU, disk, or database pressure can turn navigation latency
into false trajectory failures.
Large asset downloads, image imports, archive hashing, and other sustained disk or network jobs on
the measurement host must also be paused while a qualification or formal comparison shard runs.
If such work overlaps a shard, the complete shard is retained as `invalid_runtime` and rerun from
its preregistered task list; individual outcomes are never retained selectively.

The current official site images do not share one CPU architecture. Shopping, Shopping Admin,
Reddit, GitLab, and Map are `linux/amd64`; Wikipedia is `linux/arm64`. Here `amd64` means the x86-64
instruction set and does not require an AMD processor: a native Intel x86-64 host is equivalent for
this filter. Four recorded tasks use Map and Wikipedia together. Three more tasks (678, 760, and 811) are
excluded before execution because WebArena-Verified v1.2.3 makes their expected GET XHR unreachable:
for a navigate+GET task it selects only the last main-frame navigation before matching the expected
URL in tasks 678 and 760. Task 811 is independently unscoreable because its one-item expected JSON
array is rejected as an invalid alternatives list before event comparison. The qualification
controller runs the remaining 172 candidates natively on amd64. It does not
start or emulate the arm64 Wikipedia image.
Cross-architecture emulation is rejected: during local validation, the Shopping Admin PHP-FPM
process reproducibly crashed under amd64 emulation on an arm64 Docker daemon.

The first single-host amd64 run is pre-registered in
`evaluation/browser-use-public/webarena-amd64-scope.json`: it includes 172 candidates and excludes
the four mixed Map/Wikipedia tasks (97, 268, 426, and 741) plus the two independently reproduced
GET-XHR evaluator defects and the independently reproduced array-schema defect in task 811. The
defect evidence is pinned in
`evaluation/browser-use-public/webarena-evaluator-exclusions.json`. This engine-independent scope is
fixed before qualification and browser measurement; failures cannot remove further tasks afterward.

Stateful sites are reset by removing and recreating their container from the pinned image before
every attempt. The env-ctrl `/restart` operation is not a data reset and is not used as one. Map and
Wikipedia are read-only in this 179-task candidate set, so their initialized data volumes may remain
mounted while health is checked before each attempt. Every runtime image is bound to
`evaluation/browser-use-public/webarena-environment-images.json`; mutable `latest` tags are never
accepted as evidence.

Reset readiness requires both the env-ctrl status and two consecutive successful requests to the
browser-facing site URL. This warms cold application services and prevents a control-plane-ready but
unresponsive page from being mislabeled as a trajectory failure.

The browser-facing hostname in the runtime config must be reachable from the browser containers and
must also be passed to the WebArena container manager so generated redirects, cookies, and links use
the same origin. On rootless Docker for Linux, `host.docker.internal` may resolve to a gateway whose
host-loopback access is deliberately disabled. Use an explicit host address reachable from the
rootless network (or an equivalently isolated routable hostname) instead of weakening that daemon
setting. Keep host-specific addresses in the external runtime config and measured evidence, not in
the canonical suite. Validate the choice from a disposable container: the page must load and any
redirect or cookie domain must remain on the configured origin.

GitLab needs one additional port rule when the public host port differs from upstream port 8023:
the harness maps the chosen host port to the image's internal 8023 listener. Unlike Shopping,
Shopping Admin and Reddit, the GitLab image entrypoint does not run `env-ctrl init`; its fallback
reconfiguration also changes nginx's internal listener to the public port. The harness therefore
sets `WA_ENV_CTRL_SKIP_RECONFIGURE=true` only for GitLab, waits for the baked 8023 service to become
healthy, and then runs the image's official `env-ctrl init --base-url` exactly once. Other stateful
fixtures continue to receive `WA_ENV_CTRL_EXTERNAL_SITE_URL` and use their entrypoint's single
initialization. Magento's official initialization command timeout is raised from 180 to 600 seconds
because a timed-out cache/database update can leave the control plane healthy while the baked CMU
origin remains active; the harness requires the initializer's `[OK]` receipt rather than inferring
success from process exit. Qualification then requires two public-site responses whose final origin still
matches the configured origin. Docker health alone, or an HTTP response that redirects to the
image's baked CMU origin, is insufficient.

`site_docker_host` may point to an absolute Unix socket when browser and site containers deliberately
use different daemons. Resource limits are validated against the browser daemon; image identity,
reset, and site state are validated against the site daemon. This permits a rootful, CPU-controlled
browser runtime to reach rootless WebArena sites whose storage remains under `/data`, without moving
or restarting unrelated rootful Docker workloads.

On a rootless daemon, synchronous deletion of a Magento or GitLab writable layer can take hours.
`defer_stateful_container_removal` may therefore be enabled in the external runtime config. Reset
then stops and uniquely renames the previous container before creating a new container from the same
immutable pinned image and fixed ports. The official stateful images must have no shared writable
mounts; the reset receipt records every retired container. Garbage collection is deferred until the
declared run is complete, so deletion latency cannot enter an individual browser sample or cause
state reuse.

The site runtime is not tied to trajectory replay. The pinned images, URLs, test credentials,
health checks, reset behavior, login preparation, and WebArena-Verified evaluator form one shared
environment contract. A later autonomous-agent runner must reuse that contract and the same evidence
layout; it replaces only the action producer. Deterministic replay reads declared trajectory actions,
while an agent produces actions online. Neither path may maintain a separate copy of the WebArena
sites or scoring rules.

`scripts.manage_webarena_environment` remains the operator interface for setting up large read-only
assets and starting, inspecting, or stopping a pinned site. Site data may live on the rootless Docker
daemon under `/data`; measured browsers and agent containers remain on the resource-controlled
browser daemon. This daemon split is a storage and isolation choice, not a benchmark-specific fork.

## Formal measurement

Only eligible tasks enter the paired run. Chromium and Moli receive the same task revision,
trajectory hash, site image, initial state, authentication, viewport, limits, and evaluator. Each
task runs once per engine; only a failure receives one retry. The primary metrics are first-attempt
trajectory completion, first-attempt evaluator success, and their conjunction. Retry recovery and
final success are reported separately.

WebArena sites are reset before every engine attempt. Setup, reset, login, and initial URL opening
are excluded from measured browser time. The action path itself and its network traffic are
included. Resource comparisons use only task pairs that both engines pass on their first attempt.

The repository-curated suite and public WebArena suite remain separate report tracks and never
share a success-rate denominator.

## Operator sequence

Download and audit all public traces, then build the common suite:

```text
uv run --frozen python -m scripts.fetch_webarena_traces --output <evidence>/traces --jobs 8
uv run --frozen python -m scripts.audit_webarena_traces --trace-dir <evidence>/traces --output <evidence>/trace-audit.json
uv run --frozen python -m scripts.build_webarena_suite --trace-dir <evidence>/traces --output evaluation/browser-use-public/webarena-human-suite.json
```

Run qualification in site-compatible batches with Chromium only. `--task-ids` contains the
pre-registered IDs for the active site group:

```text
uv run --frozen python -m harness.browser_use.cli --suite evaluation/browser-use-public/webarena-human-suite.json --webarena-config <runtime-config> --qualification-run --engines chromium --failure-retries 0 --task-ids <ids> --output <evidence>/qualification/<batch>
```

Codex may first use a warm-site authoring run to inspect and repair pending retrieve-only
trajectories in batches. This mode resets each involved site once, but still creates a fresh browser
context for every task. It is diagnostic only: its manifest and attempts are explicitly marked
`trajectory_authoring_run`, it cannot include state-changing tasks or retries, and its output is
never accepted by the qualification gate. Every proposed trajectory must subsequently pass a clean
Chromium qualification run with the normal per-task reset and complete HAR, screenshot, final-page,
and evaluator evidence.

```text
uv run --frozen python -m harness.browser_use.cli --suite <evidence>/authoring/retrieve-suite.json --webarena-config <runtime-config> --trajectory-authoring-run --engines chromium --failure-retries 0 --output <evidence>/authoring/retrieve
```

For the pre-registered single-host amd64 run, select an immutable batch directly. The CLI verifies
that the scope is bound to the exact suite SHA before starting Docker:

```text
uv run --frozen python -m harness.browser_use.cli --suite evaluation/browser-use-public/webarena-human-suite.json --native-scope evaluation/browser-use-public/webarena-amd64-scope.json --batch-id shopping --webarena-config <runtime-config> --qualification-run --engines chromium --failure-retries 0 --output <evidence>/qualification/shopping
```

The native scope pre-registers stable round-robin shards with at most 12 tasks each: 18 shards cover
the 172-task amd64 set exactly once. Run every zero-based shard index for the declared count; the CLI
rejects a different count, and each manifest records the scope hash, batch, shard count, and shard
index. Shards from one stateful site remain sequential because they share a resettable service state.

```text
uv run --frozen python -m harness.browser_use.cli --suite evaluation/browser-use-public/webarena-human-suite.json --native-scope evaluation/browser-use-public/webarena-amd64-scope.json --batch-id shopping --batch-shard-count 4 --batch-shard-index 0 --webarena-config <runtime-config> --qualification-run --engines chromium --failure-retries 0 --output <evidence>/qualification/shopping-0-of-4
```

The scope orchestrator runs all 18 shards sequentially. On restart it skips an existing shard only
after verifying its manifest contract, artifact hashes, and exactly one primary Chromium attempt
per task. It refuses to overwrite incomplete or conflicting evidence. After all 172 tasks are
present, it writes the immutable correction queue; `--dry-run` prints the full plan without creating
the evidence root.

```text
uv run --frozen python -m scripts.run_webarena_qualification --suite evaluation/browser-use-public/webarena-human-suite.json --native-scope evaluation/browser-use-public/webarena-amd64-scope.json --webarena-config <runtime-config> --env-file .env --browser-docker-host unix:///var/run/docker.sock --evidence-root <evidence>/qualification
```

Merge the batch attempt files into one correction queue. A repaired or observation-annotated task
is accepted only when its adjudication points to a primary passing Chromium `attempt.json` whose
task-contract hash matches the corrected actions, evaluator, environment, and response builder.
After Codex records proposed corrections in the queue, build the exact pending subset and run that
suite once with the same qualification flags. Copy each passing repair attempt path into the final
adjudication; do not validate a hand-edited suite whose normalized provenance differs from the
canonical result.

```text
uv run --frozen python -m scripts.build_webarena_repair_queue --suite evaluation/browser-use-public/webarena-human-suite.json --attempts <batch-1>/attempts.jsonl <batch-2>/attempts.jsonl --output <evidence>/qualification/queue.json
uv run --frozen python -m scripts.build_webarena_repair_validation_suite --suite evaluation/browser-use-public/webarena-human-suite.json --qualification <evidence>/qualification/proposed.json --output <evidence>/qualification/repair-suite.json
uv run --frozen python -m harness.browser_use.cli --suite <evidence>/qualification/repair-suite.json --webarena-config <runtime-config> --qualification-run --engines chromium --failure-retries 0 --output <evidence>/qualification/repairs
uv run --frozen python -m scripts.apply_webarena_qualification --suite evaluation/browser-use-public/webarena-human-suite.json --qualification <evidence>/qualification/adjudicated.json --output evaluation/browser-use-public/webarena-human-suite.json
```
