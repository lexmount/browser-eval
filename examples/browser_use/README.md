# Browser Use Evaluation Harness

This harness compares local Chromium and Moli without measuring host-wide
resource noise. It starts one isolated browser container per engine. A normal
host-side controller reads only each target container's Docker stats.

## One-command reproduction

Requirements:

- Linux with Docker Engine and cgroup v2, or Docker Desktop/Colima backed by a
  Linux VM
- Python 3.11+ with venv support, at least 4 CPU cores, 6 GiB available memory,
  and Internet access (install python3-venv on minimal Ubuntu)
- Docker access for the current user

Run:

    ./examples/browser_use/run.sh

The first run builds two images, downloads pinned Moli v1.0.6, and creates a
dedicated Python virtual environment. No Codex, coding agent, LLM API key,
browser account, website login, privileged container, or host filesystem mount
is required.

Both Dockerfiles pin the same Debian multi-architecture base-image digest. The
run manifest records the resulting image IDs and browser versions so a
reproducer can identify binary drift; Debian security packages and public-site
content remain external variables rather than being silently treated as fixed.

Outputs:

- raw attempts, manifest, and summary under artifacts/browser_use/latest/
- latest-only Markdown and HTML reports under docs/reports/

## Protocol

- Both browser containers receive the same 2 CPU, 2 GiB memory, 512 MiB shared
  memory, and 512 PID limits by default.
- Each engine receives its own temporary Docker bridge with no peer containers;
  its CDP port is published only on host loopback. The bridge preserves public
  web access without exposing CDP to containers on Docker's default bridge.
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
- Attempt order alternates Chromium→Moli and Moli→Chromium.
- CPU and network use per-container cumulative counter deltas from Docker Engine.
  Memory and cgroup PID/thread count use 100 ms one-shot per-container stats
  samples. The controller is outside the browser containers and excluded from
  measurements.
- Functional scores report every attempt. Resource comparison uses only the
  same task/attempt pairs passed by both engines.
- Cold start is intentionally separate from this warm-browser report.
- State reset removes page/site history but cannot force a warm browser process
  to return allocator-reserved memory to its first-run footprint. The report's
  first-versus-last drift check makes retained memory and runtime accumulation
  visible instead of hiding them with browser restarts.

Override defaults with BROWSER_USE_EVAL_REPEATS, BROWSER_USE_EVAL_SAMPLE_INTERVAL_MS,
BROWSER_USE_EVAL_SETTLE_MS, BROWSER_USE_EVAL_CPUS, BROWSER_USE_EVAL_MEMORY, and
BROWSER_USE_EVAL_OUTPUT_DIR.

## Complexity tiers

- step_01_05: 1–5 browser operations
- step_06_10: 6–10
- step_11_15: 11–15
- step_16_20: 16–20

The initial suite has ten public, no-login tasks across all four tiers.

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
