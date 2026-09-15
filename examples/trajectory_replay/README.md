# Browser Trajectory Replay

This deterministic microbenchmark replays a fixed BrowserTool action trajectory
against local Chromium and Moli. It starts one isolated browser container per
engine, and a host-side controller reads only each target container's Docker
stats.

## One-command reproduction

Requirements:

- Linux with Docker Engine and cgroup v2, or Docker Desktop/Colima backed by a
  Linux VM
- Python 3.11+ with venv support, at least 4 CPU cores, 6 GiB available memory,
  and Internet access for dependencies, images, and measured public websites
- Docker access for the current user

Run:

    ./examples/trajectory_replay/run.sh

Run the small real-container smoke test before a release (requires the two
evaluation images, which the command above builds):

    RUN_BROWSER_USE_SMOKE=1 .venv/bin/python -m pytest -q -m real_smoke

Smoke is intentionally minimal: one real public-page case, once per engine. It
reuses existing evaluation images and is not a performance benchmark.

The first run builds two images, downloads pinned Moli v1.1.3, and creates a
dedicated Python virtual environment. No Codex, coding agent, LLM API key,
browser account, website login, privileged container, or host filesystem mount
is required.

Both Dockerfiles pin the same Debian multi-architecture base-image digest. The
run manifest records the resulting image IDs and browser versions so a
reproducer can identify binary drift; Debian security packages and public-site
content remain external variables rather than being silently treated as fixed.

Outputs:

- raw attempts, manifest, and summary under artifacts/trajectory_replay/latest/
- latest-only Markdown, HTML, and audit data under docs/reports/ (ignored by Git)

The complete run and report are code-driven: `run.sh` writes raw
`attempts.jsonl`, `metrics.py` calculates every aggregate and paired ratio,
`summary.json` stores the auditable values, and `report.py` only renders them.
To recalculate all metrics and HTML from existing raw attempts without running
the browsers again:

    uv run python examples/trajectory_replay/rebuild_report.py

Independent raw-data audit (does not import the aggregation implementation):

    uv run python examples/trajectory_replay/verify_metrics.py --artifacts artifacts/trajectory_replay/latest

`run.sh` runs this audit automatically. It checks correctness counts, the
successful-task intersection, 81 aggregate/ratio metric groups, and every
paired ratio's task identity and operands. Any mismatch exits with an error.

Neither command invokes Codex or another coding agent for scoring or
statistics.

## Protocol

- Element-targeted calls require a non-empty string `selector`. Snapshot `ref`
  identifiers are not resolved by this deterministic runner.
- Core attempts, summary, and manifest are saved before failure diagnostics.
  Optional LLM diagnostic errors preserve rule-based findings and scoring; the
  diagnostic status and exception type are recorded without remote error text.

- Both browser containers receive the same 2 CPU, 2 GiB memory, 512 MiB shared
  memory, and 512 PID limits by default.
- Each new page uses a 1280 × 900 CSS-pixel viewport. The runner checks actual
  `innerWidth`/`innerHeight` before measuring each task and fails on mismatch.
- Each engine receives its own temporary Docker bridge with no peer containers;
  its CDP port is published only on host loopback. The bridge preserves public
  web access without exposing CDP to containers on Docker's default bridge.
  The active suite uses public HTTPS pages, not local fixtures.
- Chromium runs as a non-root user with Docker isolation and
  no-new-privileges. Its unavailable nested setuid browser sandbox is disabled;
  the container boundary is therefore security-relevant and these evaluation
  images should not be treated as a general-purpose hostile-site sandbox.
- Both remain warm for the complete run.
- https://example.com is an excluded warm-up.
- Browser process and context remain warm. Every top-level HTTP(S) navigation
  origin is inventoried. Before the next measured task, the harness clears all
  data for those origins plus cookies, permissions, local/session storage,
  IndexedDB, Cache Storage, Service Workers, HTTP cache, and navigation history.
  Renderer GC runs, then only the old page is closed and a new blank page is
  created. A 1500 ms quiescence window follows. Page replacement, reset, and
  quiescence are excluded; the configured value is recorded in the manifest.
- Engine order alternates by task: Chromium→Moli, then Moli→Chromium.
- CPU and network totals use per-container cumulative counter deltas from Docker
  Engine. Memory, PID/thread count, CPU utilization, and network rates use
  100 ms one-shot per-container samples. The report includes time-weighted mean,
  within-attempt P95, peak, retained memory, MiB-seconds, totals, and rates.
- Each task runs once per engine. Only a failure triggers a retry (one by
  default). The report presents one final result per task and provider; all
  attempt evidence is available on its linked detail page. Result correctness
  is the primary score; result plus completion
  of the fixed reference path is reported separately. Resource comparison uses
  only tasks both engines passed on the first attempt; retries and failed tasks
  do not enter the normal-path resource baseline.
- Cold start is intentionally separate from this warm-browser report.
- State reset removes page/site history but cannot force a warm browser process
  to return allocator-reserved memory to its first-run footprint. The report's
  retained-memory and MiB-second metrics keep this visible.

Failures retry once by default. Override defaults with
BROWSER_USE_EVAL_FAILURE_RETRIES, BROWSER_USE_EVAL_SAMPLE_INTERVAL_MS,
BROWSER_USE_EVAL_SETTLE_MS, BROWSER_USE_EVAL_CPUS, BROWSER_USE_EVAL_MEMORY, and
BROWSER_USE_EVAL_OUTPUT_DIR.

Rule-based failure classification is always enabled. Optional LLM root-cause
analysis is enabled with `BROWSER_USE_EVAL_LLM_DIAGNOSTICS=1`; it uses
`gpt-5.6-sol` with low reasoning by default and requires `OPENAI_API_KEY` plus
`OPENAI_BASE_URL`. The LLM receives only failed-task evidence, produces a
traceable hypothesis, and never changes pass/fail scoring.

## Browser-tool call count

One step is one declared BrowserTool-equivalent call: navigate, click, fill,
get_text, snapshot, scroll, back, or forward. Internal page-readiness and
locator waits are not separate steps.
For form controls that navigate, `wait_for_navigation: true` explicitly waits
for the declared `wait_until` event as part of that same action. Cascading
select tasks use `domcontentloaded` so the next control's handler is ready.

The suite has thirty public-web tasks: five simple, ten medium, and fifteen
complex. Twenty-five retain the previous public suite's task definitions;
five add cascading search, infinite scrolling, site search, version/language
switching, and repository file search. Capability designs may come from
[Lexbench-Headless-Browser](https://github.com/lexmount/Lexbench-Headless-Browser)
and [Moli](https://github.com/lexmount/moli). Lexbench's local-fixture scenarios
are design references, not measured public targets. Every task
declares a fixed reference action sequence, exact expected
outputs, derivation, and source attribution. Expected outputs are unique; the
reference sequence is replayed by this provider-isolation microbenchmark but is
not claimed to be the only possible solution path. The runner judges result
correctness and result-plus-process correctness separately and programmatically;
no LLM-as-a-Judge, fuzzy contains check, or minimum-length heuristic is used. A
free-planning Agent evaluation should judge required milestones, forbidden
shortcuts, and a step budget instead of exact sequence identity.

Ground-truth annotation uses actual public-page operations through Codex
Desktop's in-app Browser Tool, followed by exact comparison of observed
outputs. `ground_truth_validation.json` is bound to the dataset SHA-256 and
records dates and provenance per task. The 2026-09-02 revision retains 25
unchanged task annotations from 2026-09-01 and adds five browser-verified
annotations; it does not claim all 30 were re-annotated that day. Benchmark
runs and statistical calculations remain entirely programmatic.

Absolute P50/P95/mean values describe each provider's cross-task distribution.
Paired ratios are computed per successful task first, then summarized. The P95
of those ratios is not a division of two marginal P95 values. Comparison cells
share one highlighting rule; rounded ties and unavailable data stay neutral.

## CubeSandbox

CubeSandbox is a valid future backend for hardware-isolated, high-volume runs:
its [official examples](https://docs.cubesandbox.com/guide/tutorials/examples.html)
run headless Chromium inside a MicroVM and expose CDP. Its
[resource metrics](https://docs.cubesandbox.com/guide/resource-metrics.html)
provide independent `host_sandbox` and `guest_workload` CPU/current-memory
views. However, the default collection interval is 5 seconds, the endpoint
retains only the latest sample, exact short-task memory peaks are not exported,
and the documented metric families do not include per-sandbox network bytes.

The initial local reference therefore uses Docker cgroup and network-namespace
accounting at 100 ms. This is a measurement boundary, not a CowAgent deployment
recommendation. A CubeSandbox backend should be added separately for production
topology, hardware isolation, and density tests; its metrics must not be blended
with Docker results.
