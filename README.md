# Browser Eval

**English** | [简体中文](docs/README.zh-CN.md)

Reproducible browser evaluations, maintained as a companion to [Moli](https://github.com/lexmount/moli). Compare Moli, other browsers and web services using declared tasks, shared acceptance rules and inspectable evidence. Browser adapters execute requests and actions; shared evaluators determine the results.

| Track | Question | Run and reproduce |
| --- | --- | --- |
| Web Fetch | Can a URL or frozen page yield the required body text, structured content or file? | [Web Fetch](harness/web_fetch/README.md) · [Frozen HTML comparisons](harness/web_fetch/HEADLESS.md) |
| Browser Trajectory Replay | Can different browsers complete the same fixed actions, and what resources do they use? | [Local suite](examples/trajectory_replay/README.md) · [Public WebArena recordings](examples/trajectory_replay/public_webarena/README.md) |
| Browser Use | Can different browsers complete tasks with the same Agent, model and tool contract? | [Run guide](examples/browser_use/README.md) |

Each track owns its tasks, denominator and publication target. Their pass rates do not combine into a single browser score. Formal experiments use the selected track's frozen configuration; the offline example below checks that the harness works.

## Run the offline example

Install Python 3.11 and [uv](https://docs.astral.sh/uv/getting-started/installation/). The initial dependency installation needs network access. The example itself uses no websites, Docker or API keys.

```bash
uv sync --frozen
uv run --frozen browser-harness-eval validate
uv run --frozen browser-harness-eval run --delay 0
```

The example contains one declared success and one declared failure. Open the absolute report path printed in the terminal to inspect tasks, results, references, outputs and errors. Output defaults to `~/.browser-eval/runs/`; set `BROWSER_EVAL_DATA_HOME` to change the data root. Adding `--fail-on-failure` returns exit code 2 for this example, so callers can test failure handling.

`run` and `validate` default to the synthetic inputs shipped with the package. Supply both configurations for your own tasks and methods:

```bash
uv run --frozen browser-harness-eval validate \
  --dataset /absolute/path/dataset.json \
  --provider-config /absolute/path/providers.json --providers my-browser
uv run --frozen browser-harness-eval run \
  --dataset /absolute/path/dataset.json \
  --provider-config /absolute/path/providers.json --providers my-browser \
  --output /absolute/path/new-evidence
```

Ordinary `run` output is diagnostic evidence. Formal Web Fetch calls must use `scripts/run_certified_benchmark.py` to freeze and recheck references; incomplete cohorts cannot be published. Models, remote APIs and live-site experiments may incur costs. See each track's guide for requirements.

Use `--task-kind file` to check file delivery, or select `body`, `structure` or `resource`. `--case-ids case-a,case-b` selects explicit tasks; combining it with a task kind takes their intersection. Reports include only the selected tasks and methods and identify diagnostic runs.

## Install the capabilities you need

The base installation supports the offline example and basic Web Fetch. Install extras for your experiment:

```bash
uv sync --frozen --extra headless-eval       # Local browsers and PDF support
uv sync --frozen --extra webmainbench-eval   # Official WebMainBench scoring
uv sync --frozen --extra browser-use-eval    # WebArena and Agent execution
uv sync --frozen --extra oss-eval            # Open-source content extractors
```

Prepare browser binaries, Docker, site deployments, models and provider accounts separately. They are not included in the Python package. Historical binary identities, Python patch versions and source commits reproduce their own experiments; they are not defaults for new comparisons.

`browser-use-eval` installs a pinned WebArena-Verified fork through Git and requires Git and network access. The lockfile and wheel use the same source; a registry package with the same version cannot replace that evaluator.

For Moli installation and browser usage, see the [Moli repository](https://github.com/lexmount/moli) and [releases](https://github.com/lexmount/moli/releases). This repository maintains evaluation protocols, adapters, scoring and evidence. Comparisons apply the same acceptance and evidence requirements to every participant.

## Tasks, methods and evidence

- **Add a method:** implement and register the existing Provider interface, preserving the shared result and scoring contracts. See the [adapter contract](docs/provider-contract.md).
- **Add a task:** declare inputs, independent references, acceptance rules and coverage. Test both valid answers and answers that must fail. See [metrics](docs/metrics.md).
- **Build a report:** use an explicit evidence directory. Builds verify hashes, call coverage and required review without calling providers again. See [architecture](docs/architecture.md).
- **Publish:** the [current Web Fetch profile](evaluation/current-report.json) binds its matrix and target. Full WebMainBench, WCXB, Browser Use and trajectory replay have separate profiles. Their files own the site and expected version.

Reports currently default to Chinese. English report generation requires explicit selection; when absent, the English entry opens a notice with a return link. Repository documentation is primarily English, with a Chinese README. Frozen rules, source quotations and evidence retain their original language.

## Contribute

PRs are welcome: add browser adapters, tasks, regression tests, scoring corrections, documentation improvements or README translations. Apply fixes through shared contracts rather than lowering requirements for one browser. Start with the [contribution guide](CONTRIBUTING.md), or [open an issue](https://github.com/lexmount/browser-eval/issues) with a reproducible example.

```bash
make setup-test   # Full Python and Node test dependencies
make ci           # Python, UI, native-adapter, offline and package checks
```

CI builds the distribution and exercises it outside the checkout. Real container and online-endpoint tests need their declared environment; offline checks do not reproduce a live experiment. See [contributing](CONTRIBUTING.md) and [security](SECURITY.md).

Complete historical Web Fetch, popular-site, dynamic-content and access-restriction reference inputs are stored separately in private data directories. Each `source-inputs.json` binds them; `dataset-catalog.json` retains task inventories and definition hashes. These inputs are not shipped or downloaded automatically. Import an approved copy using the [reference-input guide](docs/reference-inputs.md). `make test-reference-inputs` verifies every manifest and fails if any required input set is missing.

Git contains reusable code, compact task definitions, acceptance contracts and evidence bindings. Provider responses, execution manifests, authored reports, screenshots and publication bundles stay outside Git. Third-party data rights are separate from the code license; see [third-party content](THIRD_PARTY.md).

Project-owned code uses the [MIT license](LICENSE). Existing third-party notices continue to apply.
