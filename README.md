# Browser Service Eval

Reproducible evaluation of browser engines and services across public task
suites, functional correctness, latency, output size, CPU, memory, and network
traffic.

The first backend compares warm local Chromium and Moli in equal, isolated
Docker cgroups. It does not require Codex, another coding agent, an LLM API key,
website accounts, or login state.

## Run the browser evaluation

Requirements: Docker Engine (cgroup v2), Python 3.11+ with `venv`, at least 4
CPU cores, 6 GiB available memory, and Internet access.

```bash
./examples/browser_use/run.sh
```

The command builds pinned browser images, installs the controller in a local
virtual environment, runs the versioned 10-task public suite, and writes:

- raw reproducibility artifacts to `artifacts/browser_use/latest/` (ignored by Git)
- latest-only Markdown and HTML reports to `docs/reports/`

See [examples/browser_use/README.md](examples/browser_use/README.md) for the
measurement protocol, complexity tiers, overrides, and CubeSandbox decision.

## Harness layout

- `harness/browser_use`: interactive browser-use execution, metrics, and reports
- `harness/web_fetch`: reserved sibling capability for Web Fetch evaluation

Browser Use and Web Fetch intentionally keep separate suites and metrics. Web
Fetch is not implemented in this first delivery; its directory documents the
boundary instead of exposing a placeholder API.
