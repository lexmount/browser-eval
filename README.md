<div align="center">

<img src="docs/assets/overview.png" alt="Browser Eval — Same tasks. Clear comparisons." width="100%">

**English** · [简体中文](docs/README.zh-CN.md)

[![CI](https://github.com/lexmount/browser-eval/actions/workflows/ci.yml/badge.svg)](https://github.com/lexmount/browser-eval/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/code-Apache--2.0-51735b)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-356a91)](pyproject.toml)

[Quickstart](#quickstart) · [Tracks](#evaluation-tracks) · [Reports](#reports) · [Contributing](CONTRIBUTING.md)

</div>

Browser Eval is an extensible evaluation harness for browsers and web agents. It combines task contracts, adapters, saved evidence and scoring to produce inspectable reports.

## Evaluation tracks

The following workflows have runnable guides. Additional workflows, including Web Search, can use the same adapter and task-contract model; see [contributing](CONTRIBUTING.md).

| Track | What it tests | Guide |
| --- | --- | --- |
| Web Fetch | Retrieve the requested page content, records, resources or files | [Local example](examples/web_fetch/README.md) · [Full guide](harness/web_fetch/README.md) |
| Browser Trajectory Replay | Complete the same recorded browser actions across implementations | [Curated suite](examples/trajectory_replay/README.md) · [WebArena replay](examples/trajectory_replay/public_webarena/README.md) |
| Browser Use | Let an Agent use different browsers to complete tasks | [CowAgent + WebArena](examples/browser_use/README.md) |

Web Fetch supports local browsers, hosted services and extraction tools. See [adapters](docs/provider-contract.md) for available methods and [dependencies](docs/dependencies.md) for installation requirements. Replay and Browser Use have separate runners and environments.

Task definitions and scoring come from the selected benchmark: [LexBench Web Fetch](https://github.com/lexmount/lexbench-webfetch), WebMainBench, WCXB or WebArena. See [benchmark integration](docs/benchmarks.md).

## Quickstart

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run the offline example:

```sh
git clone https://github.com/lexmount/browser-eval.git
cd browser-eval
uv sync --frozen
uv run --frozen browser-harness-eval validate
uv run --frozen browser-harness-eval run --delay 0
```

Open the report path printed in the terminal. The example has one expected pass and one expected failure; it needs no browser, Docker or API key. Python 3.11–3.14 is supported; development uses 3.11.

Next, [try HTTP, Moli or Chromium on a local article](examples/web_fetch/README.md). Optional adapters, service credentials and live evaluation commands are covered in the track guides above.

The reusable ten-method Web Fetch cohort is selected with `--providers configured --provider-config configs/providers.example.json`; shared defaults are 30 seconds per call and one failure retry. See the [provider contract](docs/provider-contract.md#selected-native-cohort).

## Reports

Reports include comparisons, task results, full responses and failure evidence. Runs are saved under `~/.browser-eval/runs/`; use `--output /absolute/path/new-run` to choose another directory.

Rebuild from saved evidence without repeating calls:

```sh
uv run --frozen browser-harness-eval report /absolute/path/to/run
```

English is the default; add `--language zh` when running or rebuilding for Chinese. A rebuild without that option keeps the saved language.

Each new report records its Browser Eval build and selected benchmark edition, with exact code and input hashes. See [versioning](docs/benchmarks.md#benchmark-editions) for comparison rules and [reference inputs](docs/reference-inputs.md) for historical data.

To share a report online, publish its `report/` directory through [Artifact Site](https://github.com/lexmount/artifact-site), following the [hosting guide](docs/report-hosting.md). Choose access permissions there. Formal benchmark releases also require the checks in their track guide.

## Contribute

Read [CONTRIBUTING](CONTRIBUTING.md) for code ownership, tests and PR conventions, or [open an issue](https://github.com/lexmount/browser-eval/issues) with a reproducible example.

```sh
make setup-test
make ci
```

[Architecture](docs/architecture.md) · [Security](SECURITY.md) · [Third-party content](THIRD_PARTY.md) · [Apache 2.0](LICENSE)

## Cite Browser Eval

Use this citation or [CITATION.cff](CITATION.cff), and record the commit used.

```bibtex
@software{browser_eval_2026,
  author = {{Browser Eval contributors}},
  title = {Browser Eval},
  year = {2026},
  version = {0.2.0},
  url = {https://github.com/lexmount/browser-eval}
}
```
