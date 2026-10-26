<div align="center">

<img src="docs/assets/overview.png" alt="Browser Eval — Same tasks. Clear comparisons." width="100%">

**English** · [简体中文](docs/README.zh-CN.md)

[![CI](https://github.com/lexmount/browser-eval/actions/workflows/ci.yml/badge.svg)](https://github.com/lexmount/browser-eval/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/code-Apache--2.0-51735b)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-356a91)](pyproject.toml)

[Quickstart](#quickstart) · [Tracks](#evaluation-tracks) · [Reports](#reports) · [Versions](#versions) · [Related projects](#related-repositories) · [Contributing](CONTRIBUTING.md)

</div>

Browser Eval evaluates Web Fetch, fixed browser trajectories and autonomous Browser Use, with Web Search available in preview. Compare task results, latency and resource use through shared adapters, saved evidence and inspectable reports.

## Evaluation tracks

The main branch includes the three browser workflows below. Web Search is implemented on the development branch linked in its row; it is not included in the current main-branch package.

| Track | What it tests | Guide |
| --- | --- | --- |
| Web Fetch | Retrieve the requested page content, records, resources or files | [Local example](examples/web_fetch/README.md) · [Full guide](harness/web_fetch/README.md) |
| Web Search (preview) | Compare search results, relevance, source retrieval, latency and billing | [Implementation and integration PR #58](https://github.com/lexmount/browser-eval/pull/58) · [Search guide](https://github.com/lexmount/browser-eval/blob/codex/report-release-checks/harness/web_search/README.md) |
| Browser Trajectory Replay | Complete the same recorded browser actions across implementations | [Curated suite](examples/trajectory_replay/README.md) · [WebArena replay](examples/trajectory_replay/public_webarena/README.md) |
| Browser Use | Let an Agent use different browsers to complete tasks | [CowAgent + WebArena](examples/browser_use/README.md) |

Web Fetch supports local browsers, hosted services and extraction tools. See [adapters](docs/provider-contract.md) for available methods and [dependencies](docs/dependencies.md) for installation requirements. Replay and Browser Use have separate runners and environments.

Task definitions and scoring come from the selected benchmark: [LexBench Web Fetch](https://github.com/lexmount/lexbench-webfetch), [WebMainBench](https://github.com/opendatalab/WebMainBench), [WCXB](https://github.com/Murrough-Foley/web-content-extraction-benchmark) or [WebArena-Verified](https://github.com/ServiceNow/webarena-verified). Search tasks and scoring belong to [LexBench Web Search](https://github.com/lexmount/lexbench-websearch). See [benchmark integration](docs/benchmarks.md).

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

## Reports

Reports include comparisons, task results, full responses and failure evidence. Runs are saved under `~/.browser-eval/runs/`; use `--output /absolute/path/new-run` to choose another directory.

Rebuild from saved evidence without repeating calls:

```sh
uv run --frozen browser-harness-eval report /absolute/path/to/run
```

English is the default; add `--language zh` when running or rebuilding for Chinese. A rebuild without that option keeps the saved language.

Each new report records its Browser Eval build and selected benchmark edition, with exact code and input hashes. See [versioning](docs/benchmarks.md#benchmark-editions) for comparison rules and [reference inputs](docs/reference-inputs.md) for historical data.

To share a report online, publish its `report/` directory through [Artifact Site](https://github.com/lexmount/artifact-site), following the [hosting guide](docs/report-hosting.md). Choose access permissions there. Formal benchmark releases also require the checks in their track guide.

## Versions

The current package version is `0.2.0`. Software tags use `vMAJOR.MINOR.PATCH`; benchmark editions and hosted report versions are independent. A report records all three identities so rebuilding a report does not relabel its tasks or saved calls.

Use [the release guide](docs/releases.md) for compatibility rules, release checks and the scope of the first software release. Web Search remains a preview until its integration reaches main. A stable release commits to documented commands, input contracts and saved-evidence compatibility; it does not guarantee that every browser or live website passes every task.

## Related repositories

| Repository | Role |
| --- | --- |
| [LexBench Web Fetch](https://github.com/lexmount/lexbench-webfetch) | Versioned Web Fetch tasks, acceptance scoring and frozen input bindings |
| [LexBench Web Search](https://github.com/lexmount/lexbench-websearch) | Versioned search queries, source references and retrieval scoring |
| [Moli](https://github.com/lexmount/moli) | Browser evaluated through the same task contracts as other participants |
| [WebFetch CLI](https://github.com/lexmount/webfetch-cli) | CLI client for the Lexmount WebFetch service; separate from the evaluation harness |
| [Artifact Site](https://github.com/lexmount/artifact-site) | Report hosting, immutable versions and access controls |
| [Lexbench-Headless-Browser](https://github.com/lexmount/Lexbench-Headless-Browser) | Protocol, automation-driver and web-platform benchmarks |
| [WebMainBench fork](https://github.com/lexmount/WebMainBench/tree/lex-main) · [upstream](https://github.com/opendatalab/WebMainBench) | Frozen-HTML extraction tasks and declared scoring corrections |
| [WebArena-Verified fork](https://github.com/lexmount/webarena-verified/tree/lex-main) · [upstream](https://github.com/ServiceNow/webarena-verified) | Tasks and evaluator for Browser Use and WebArena trajectory replay |
| [WCXB](https://github.com/Murrough-Foley/web-content-extraction-benchmark) | Independent content-extraction benchmark |
| [CowAgent](https://github.com/zhayujie/CowAgent), [OpenClaw](https://github.com/openclaw/openclaw), [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | Agent execution and Web Fetch tool baselines |

[Dependency sources and pins](docs/dependencies.md) cover Playwright, extraction libraries and supporting runtimes. [Third-party notices](THIRD_PARTY.md) cover attribution and data rights. Cite the benchmark and upstream data used alongside Browser Eval.

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
