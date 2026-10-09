# Browser Trajectory Replay protocol

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
single/all text capture, table-column capture, single/all attribute capture, property capture,
snapshot, history navigation, and scroll. Readiness waits are part of an action and do not count as
additional trajectory steps.

Every declared action has the same 30-second default timeout in both datasets and both engines;
tasks may declare a different timeout in their trajectory. Duration is still measured continuously.
This avoids turning a 5-second application navigation delay into a false trajectory failure.
Each task also has a 300-second wall-clock budget. An action receives the smaller of its declared
timeout and the remaining task budget, so a broken multi-step path cannot accumulate independent
action timeouts into an unbounded run. The same budget applies to both engines.

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
observed response builder, then passes two independent Chromium qualification runs.

All 172 candidates in the pre-registered native scope are replayed in two independent passes with
upstream Chromium against the pinned WebArena-Verified environment before either measured browser
is run. Each pass uses a clean per-task reset and contains one primary attempt, not a retry. A task
enters one of three queues only after both passes are present:

- eligible: the path executes and the current deterministic evaluator passes;
- needs response annotation: the UI path executes but a retrieve response still needs an
  independent DOM capture;
- needs trace repair: an action or final-state evaluation fails.

For correction, Codex may inspect the original trace, its snapshots, the current page, and the
failure evidence. It may change UI actions or add DOM capture steps. It may not change the task,
expected result, evaluator, or environment. A repaired task becomes eligible only after two
independent new Chromium runs pass the same WebArena-Verified evaluator. Original and corrected trajectories,
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
Qualification shards for the same site are sequential within one evidence root. Disjoint site
batches may run concurrently under distinct process locks; the orchestrator never overlaps two
batches whose reset can touch the same service. Both complete passes cover the same immutable scope.
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
The complete reset has one 900-second POSIX wall-clock deadline, including blocking Docker SDK calls;
the individual readiness checks cannot each restart that budget. A deadline breach is retained as an
`environment_prepare_failed` attempt and never repaired selectively inside a formal run.

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

Reddit has a separate upstream inconsistency: its `RedditOps._init()` requires a `base_url` argument
and does not fall back to `WA_ENV_CTRL_EXTERNAL_SITE_URL`, so the supervisor init exits before making
changes. The harness waits for that no-op attempt to exit, runs the same official initializer once
with an explicit `--base-url`, and requires its JSON success result before checking the public origin.

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

### Candidate replay inputs

Use `scripts/run_webarena_catalog.py` for declared task subsets and candidate action
revisions. Its catalog, action overrides, environment inputs and evidence validation
use the same contracts as complete catalog runs. A diagnostic subset does not establish
a formal complete-cohort score.

The historical 36-task input protocol remains available through
`scripts/freeze_original36_candidate_cohort.py`. `--reference-root` resolves its relative
candidate paths without depending on the working directory. Overrides are hash-bound;
when a stabilization names a different qualified parent, `stabilization_reversions`
declares the one-based action index, current action type and exact parent call for each
change. Reconstructing those calls and their canonical digest must reproduce the entire
parent file hash. Action-count changes additionally require the existing paired browser
qualification receipts. Freezing writes a new manifest and never overwrites an existing
one; it does not execute or score a browser.

### Qualification decisions and immutable outputs

`scripts/build_revised_webarena_dataset.py` creates a pending dataset revision
from `--base`, `--corrections` and repeated `--task-id` arguments, with an optional
`--selection`. Declared counts must match the source population. Fork replacements
use `--revision-bindings` (default: the repository's `dataset-revision-bindings.json`)
to bind the upstream task, predecessor trajectory, candidate actions and independent
reference by hash. Artifact paths are relative to the repository root. The shared
correction module owns this stage; adding a replacement does not require a task-ID
branch. The output requires full-scope requalification before measurement. Use a
new `--output` path; `--replace-generated` is limited to a generated revision of
the same base dataset and never authorizes replacing measured evidence.

`scripts/build_webarena_native_scope.py` selects a task population before
qualification or measurement from the declared site image architectures and
evidence-backed evaluator exclusions. Use explicit `--suite`, `--image-lock`
and `--evaluator-exclusions` inputs for a new experiment. The qualification
shard size is configurable with `--max-tasks-per-shard`; task and browser counts
are not encoded in the implementation. The command writes only a new output.

`scripts/revise_webarena_method_inputs.py` freezes a reviewed trajectory revision
from `--prior`, `--revisions` and `--site-map`. Each changed task must identify
its predecessor action hash, candidate and cause. The compiler checks actual
predecessor actions, candidate hashes and stable source files, then writes a new
manifest without modifying prior evidence. A revision that changes action count
requires an explicit readiness migration when readiness rules are present.

The public WebArena queue, revalidation, finalization and application commands share
`public_webarena/qualification.py`. Every declared task and decision must occur exactly
once. A resumable shard must retain its raw `attempt.json` as well as its summary and HAR.
A JSONL attempt projection must match that raw record, including scalar types; a passing summary
cannot promote a failed attempt, and a failing summary cannot discard a passing attempt.
Older summaries without raw source evidence cannot establish eligibility and require
evidence revalidation.
Eligibility requires two distinct Chromium runs covering integer pass indices 1 and 2,
with the required HAR and hash-verified final-page evidence. Linked attempt, HAR and
final-page files are rejected. Applied eligibility retains both distinct proof hashes;
empty trajectories cannot gain eligibility from passing metadata.

Finalization allows declared trajectory and response-builder repairs while preserving
the task instruction, evaluator contracts and environment. Applying decisions derives
a new suite and preserves the original input, including when validation fails. The queue
and final adjudication are immutable private evidence; derived suites are validated before
the output appears. Repeating a command with identical content is safe, but a different
revision needs a new output path. These commands do not execute a browser or alter
historical comparison scores.

### Public WebArena repair proposals

Repair authoring is optional. It produces pending trajectories for the isolated public
WebArena track and does not modify canonical tasks, measured outcomes or qualifications.
`diagnostics.py` builds the authoring input and runs bounded read-only Codex jobs;
`qualification.py` derives and validates the pending probe or batch.

The six `scripts/propose_webarena_*.py` commands use that same qualification module.
Use action repairs for exact guarded splices, target replacement for a stale locator,
and capture or observed repairs for answers collected from declared DOM observations.
Evaluator-fill proposals use exact official payload values; auto-wait proposals require
a matching failed action, a supported post-action timeout and a current task-contract
binding. Regenerate old unbound qualification queues from their source attempts before
using them for auto-wait proposals. These tools propose
changes; they do not certify them or revise official scores.

Proposal commands require a new output file or, for batches, a new output directory.
They reject linked paths and publish a batch only after every selected task succeeds.
Outputs bind input hashes, the original trajectory and the executable task contract;
applying a proposal after that trajectory or evaluator contract changes fails. Keep
these generated proposals outside Git alongside private qualification evidence.

Build a packet with `scripts/build_webarena_repair_packet.py --suite <suite>
--attempt <attempt.json> --output <packet.json>`. Packet version 2 separates raw attempt
provenance from the three author-readable evidence references: screenshot, final page
and sanitized HAR. The job receives a packet without the raw attempt path. The program
checks the complete packet against its frozen source before launch. Version 1 packets
must be regenerated from their original inputs; they are retained as historical evidence
and are not accepted for new jobs. Read-only execution and these authoring instructions
do not provide operating-system isolation from every readable file on the host.

`scripts/run_webarena_codex_repairs.py --suite <suite> --packet <packet.json>
--output-root <new-directory>` accepts repeated `--packet` arguments, `--max-workers`,
`--timeout-seconds`, `--codex-binary`, `--model` and `--reasoning-effort`. Its receipts bind
the selected configuration and input hashes. Failed jobs and inputs that change during
execution leave an incomplete batch rather than a qualification claim.

Use `scripts/build_webarena_repair_probe.py` for one proposal,
`scripts/build_webarena_repair_batch.py` for a general batch, or
`scripts/build_webarena_codex_repair_suite.py` for the existing Codex two-pass validation
protocol. Each takes `--suite`, `--proposal` and `--output`; batch commands accept repeated
proposals. They validate the resulting suite before exposing a new immutable output.
Two independent qualification passes remain required by the validation protocol.

### Remote mutation diagnostics

`scripts/reevaluate_webarena_evaluator_transition.py` checks saved passing
evidence against the pinned evaluator after an explicitly requested transition.
It reads declared `--run` directories and writes only a new `--output` receipt.
Scope, site map, run inputs, progress and consumed evidence must remain unchanged
throughout the build. Explicit exclusions bind the task, old evaluation hash and
run-input hash; an exclusion absent from the source passes is rejected. This
command does not replace historical verdicts or execute browser actions.

`scripts/rescore_saved_retrieval_attempt.py` evaluates a complete saved retrieval
against an explicitly supplied dataset without browser calls. It reads the current
task-local result, an agreeing cohort receipt, or the older standalone result
layout; missing action counts and conflicting records are rejected. The saved
response, HAR and declared evidence must match their hashes and remain unchanged
during evaluation. Its new output is diagnostic evidence and does not replace
the saved verdict or certify a new experiment.

For a declared corrective rerun, `scripts/supersede_webarena_task_browser_results.py`
requires the complete `--replace TASK_ID/ENGINE` scope and an evidenced `--reason`.
It checks the shared protocol, browser identity, task contract, attempt sequence,
raw records and bound page evidence. The new run retains every source call and
its original bytes under `run/source-evidence/`; `run/source-inputs.json` binds
those files by path and hash. Current attempt locations are rebound to the new
run with their predecessor hashes, while verdicts, timings and responses remain
unchanged. Reports and evidence are staged together; failed checks or rendering
leave no partial output. A later correction must also verify the retained
history. Formal publication still requires the corresponding new profile and
release gates; this command does not update historical publication bindings.

Offline input and evidence audits use the maintained diagnostic modules:

- `scripts/audit_replay_comparison_inputs.py` inventories the declared comparison
  matrix, contract changes and optional upstream-fork differences.
- `scripts/audit_replay_readiness.py` locates adjacent disabled-button snapshots
  and clicks. It preserves historical verdicts; a snapshot does not establish
  the target's state when the click was dispatched.
- `scripts/audit_saved_replay_correction.py` compares correction catalogs on saved
  evidence. An incomplete execution is never rescored with an invented response.
- `scripts/audit_replay_cart_assertion.py` runs isolated DOM counterexamples with
  an explicit browser binary and blocked page requests. It requires Playwright;
  its results are assertion diagnostics rather than task scores.
- The isolated public WebArena track uses
  `scripts/audit_webarena_recorded_outcomes.py` and
  `scripts/verify_webarena_evaluator_exclusions.py` for hash-bound recording audits.
  Recording audits inject the official expected answer to isolate network
  evaluation; they do not represent current-environment browser execution.

File-producing audits require a new output path and reject linked paths before
execution. They reject inputs that change during inspection. Keep generated
diagnostics outside Git. Basic log and input audits do not import Playwright or
WebArena; those dependencies are loaded only by the operations that need them.

`scripts/run_remote_mutation_diagnostic.py` exposes the shared diagnostic pipeline. With
`--emit-proposal` it appends a compiled completion observer to the unchanged canonical actions;
`--check-proposal` verifies that proposal without opening a browser. Both modes require the frozen
seed manifest and its matching qualified dataset under an explicit `--source-root`.

Physical execution additionally requires a task-bound reset receipt, site map, browser binary,
CowAgent checkout and dedicated local CDP endpoint. Only a verified compiled final observer can
be retried after the exact declared navigation-context error. The retry count and navigation
readiness timeout must be bounded; canonical mutation actions are executed at most once.
Frozen inputs are checked before execution and after cleanup. Failures retain traces, available
network evidence and a terminal diagnostic receipt. A diagnostic pass is never a formal benchmark
result or a qualification promotion.

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

Run the frozen eligible subset against both engines. The loader admits only tasks already marked
`eligible`; skipped candidates remain in the canonical suite for audit but never enter the formal
denominator:

```text
uv run --frozen python -m harness.trajectory_replay.public_webarena.cli --suite evaluation/browser-use-public/webarena-human-suite.json --webarena-config <runtime-config> --env-file .env --browser-docker-host unix:///var/run/docker.sock --engines chromium,moli --failure-retries 1 --output <evidence>/formal/webarena-public-trajectories/run --reports <evidence>/formal/webarena-public-trajectories/reports
```

After the run, audit the physical-attempt matrix and rebuild the current HTML from the immutable raw
attempts. Rebuilding accepts the manifest-selected subset but fails if any attempt task lies outside
the suite or differs from the manifest:

```text
uv run --frozen python examples/trajectory_replay/public_webarena/verify_metrics.py --artifacts <evidence>/formal/webarena-public-trajectories/run
uv run --frozen python examples/trajectory_replay/public_webarena/rebuild_report.py --suite evaluation/browser-use-public/webarena-human-suite.json --artifacts <evidence>/formal/webarena-public-trajectories/run --reports <evidence>/formal/webarena-public-trajectories/reports
```

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
uv run --frozen python -m harness.trajectory_replay.public_webarena.cli --suite evaluation/browser-use-public/webarena-human-suite.json --webarena-config <runtime-config> --qualification-run --qualification-pass-index <1-or-2> --qualification-required-passes 2 --engines chromium --failure-retries 0 --task-ids <ids> --output <evidence>/qualification/pass-<1-or-2>/<batch>
```

Codex may first use a warm-site authoring run to inspect and repair pending retrieve-only
trajectories in batches. This mode resets each involved site once, but still creates a fresh browser
context for every task. It is diagnostic only: its manifest and attempts are explicitly marked
`trajectory_authoring_run`, it cannot include state-changing tasks or retries, and its output is
never accepted by the qualification gate. Every proposed trajectory must subsequently pass two
clean Chromium qualification runs with the normal per-task reset and complete HAR, screenshot,
final-page, and evaluator evidence. A Codex proposal has no qualification effect by itself.

```text
uv run --frozen python -m harness.trajectory_replay.public_webarena.cli --suite <evidence>/authoring/retrieve-suite.json --webarena-config <runtime-config> --trajectory-authoring-run --engines chromium --failure-retries 0 --output <evidence>/authoring/retrieve
```

For the pre-registered single-host amd64 run, select an immutable batch directly. The CLI verifies
that the scope is bound to the exact suite SHA before starting Docker:

```text
uv run --frozen python -m harness.trajectory_replay.public_webarena.cli --suite evaluation/browser-use-public/webarena-human-suite.json --native-scope evaluation/browser-use-public/webarena-amd64-scope.json --batch-id shopping --webarena-config <runtime-config> --qualification-run --qualification-pass-index <1-or-2> --qualification-required-passes 2 --engines chromium --failure-retries 0 --output <evidence>/qualification/pass-<1-or-2>/shopping
```

The native scope pre-registers stable round-robin shards with at most 12 tasks each: 18 shards cover
the 172-task amd64 set exactly once. Run every zero-based shard index for the declared count; the CLI
rejects a different count, and each manifest records the scope hash, batch, shard count, and shard
index. Shards from one stateful site remain sequential because they share a resettable service state.

```text
uv run --frozen python -m harness.trajectory_replay.public_webarena.cli --suite evaluation/browser-use-public/webarena-human-suite.json --native-scope evaluation/browser-use-public/webarena-amd64-scope.json --batch-id shopping --batch-shard-count 4 --batch-shard-index 0 --webarena-config <runtime-config> --qualification-run --qualification-pass-index <1-or-2> --qualification-required-passes 2 --engines chromium --failure-retries 0 --output <evidence>/qualification/pass-<1-or-2>/shopping-0-of-4
```

The scope orchestrator keeps shards within each site batch sequential. One runtime configuration
permits one active batch. To run batches concurrently, repeat `--webarena-config` with independently
provisioned slice configurations and set `--max-site-workers` to the desired limit. These slices
must have separate containers, data stores, ports, gateways and lock namespaces; distinct filenames
alone do not establish deployment isolation. On restart it skips an existing shard only after
verifying its manifest contract, artifact
hashes, and exactly one primary Chromium attempt per task and pass. Incomplete output is preserved
under `_failed` before a clean rerun; conflicting complete evidence is never overwritten. After
both 172-task passes are present, it writes the immutable correction queue;
`--dry-run` prints the full plan without creating the evidence root or relocating existing
incomplete output. The suite and scope must retain their bytes throughout orchestration;
runtime gateway updates follow the existing Rainbond reset receipts. Linked shard artifacts
are rejected. Derived qualification suites are validated
before writing, and an existing different output is never replaced.

```text
uv run --frozen python -m scripts.run_webarena_qualification --suite evaluation/browser-use-public/webarena-human-suite.json --native-scope evaluation/browser-use-public/webarena-amd64-scope.json --webarena-config <runtime-config> --env-file .env --browser-docker-host unix:///var/run/docker.sock --max-site-workers 3 --evidence-root <evidence>/qualification
```

Merge the batch attempt files into one correction queue. A repaired or observation-annotated task
is accepted only when its adjudication points to two primary passing Chromium `attempt.json` files
from distinct runs covering pass indices 1 and 2. Both task-contract hashes must match the corrected
actions, evaluator, environment, source evaluator contract, and response builder.
After Codex records proposed corrections in the queue, build the exact pending subset and run that
suite twice with the pass-specific qualification flags. Copy both passing repair attempt paths into the final
adjudication; do not validate a hand-edited suite whose normalized provenance differs from the
canonical result.

```text
uv run --frozen python -m scripts.build_webarena_repair_queue --suite evaluation/browser-use-public/webarena-human-suite.json --attempts <batch-1>/attempts.jsonl <batch-2>/attempts.jsonl --output <evidence>/qualification/queue.json
uv run --frozen python -m scripts.build_webarena_repair_validation_suite --suite evaluation/browser-use-public/webarena-human-suite.json --qualification <evidence>/qualification/proposed.json --output <evidence>/qualification/repair-suite.json
uv run --frozen python -m harness.trajectory_replay.public_webarena.cli --suite <evidence>/qualification/repair-suite.json --webarena-config <runtime-config> --qualification-run --qualification-pass-index 1 --qualification-required-passes 2 --engines chromium --failure-retries 0 --output <evidence>/qualification/repairs/pass-1
uv run --frozen python -m harness.trajectory_replay.public_webarena.cli --suite <evidence>/qualification/repair-suite.json --webarena-config <runtime-config> --qualification-run --qualification-pass-index 2 --qualification-required-passes 2 --engines chromium --failure-retries 0 --output <evidence>/qualification/repairs/pass-2
uv run --frozen python -m scripts.apply_webarena_qualification --suite evaluation/browser-use-public/webarena-human-suite.json --qualification <evidence>/qualification/adjudicated.json --output evaluation/browser-use-public/webarena-human-suite.json
```
