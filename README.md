<div align="center">

<img src="docs/assets/overview.png" alt="Browser Eval — Same tasks. Clear comparisons." width="100%">

**English** · [简体中文](docs/README.zh-CN.md)

[![CI](https://github.com/lexmount/browser-eval/actions/workflows/ci.yml/badge.svg)](https://github.com/lexmount/browser-eval/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/code-Apache--2.0-51735b)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-356a91)](pyproject.toml)

[Features](#features) · [Quickstart](#quickstart) · [Tracks](#evaluation-tracks) · [Reports](#reports) · [Versions](#versions) · [Related projects](#related-repositories) · [Contributing](CONTRIBUTING.md)

</div>

Run benchmark comparisons and turn the results into reports you can inspect, rebuild and share.

## Features

| Feature | What you get |
| --- | --- |
| Run to report | Run a configured comparison and generate an HTML report with one CLI command. |
| Multiple benchmarks | Use integrated benchmarks or bring your own tasks, with the selected benchmark's scoring rules. [Benchmark integration](docs/benchmarks.md) |
| Extensible participants | Compare local browsers, hosted APIs, extraction tools and web agents. Add participants through the existing [adapter contracts](docs/provider-contract.md). |
| Inspectable evidence | Follow aggregate results down to individual responses, action traces and failure logs. |
| Saved-run workflows | Rebuild reports and rescore saved outputs without repeating provider calls. |
| Quality, performance and cost | Compare task success alongside latency, resource usage and recorded billing. |
| Reproducible comparisons | Record task inputs, benchmark and software revisions, scoring code and run settings. |
| Reports you can share | Publish through Artifact Site and choose who can access the report. [Create and share a report](#reports) |

## Evaluation tracks

Choose a workflow for its task contract, setup and runnable example.

| Track | What it tests | Benchmarks and task suites | Guide |
| --- | --- | --- | --- |
| Web Fetch | Retrieve the requested page content, records, resources or files | [LexBench Web Fetch](https://github.com/lexmount/lexbench-webfetch), [WebMainBench](https://github.com/opendatalab/WebMainBench), [WCXB](https://github.com/Murrough-Foley/web-content-extraction-benchmark) | [Local example](examples/web_fetch/README.md) · [Full guide](harness/web_fetch/README.md) |
| Web Search (preview) | Compare search results, relevance, source retrieval, latency and billing | [LexBench Web Search](https://github.com/lexmount/lexbench-websearch): scenario queries and a SimpleQA Verified retrieval adaptation | [Local example](examples/web_search/README.md) · [Full guide](harness/web_search/README.md) |
| Browser Trajectory Replay | Complete the same recorded browser actions across implementations | [Repository-curated fixed-action suite](examples/trajectory_replay/README.md), adapted from [Lexbench-Headless-Browser scenario designs](https://github.com/lexmount/Lexbench-Headless-Browser/tree/3f1580a20922fc3f339b5fc279d9bbdb06d5d194/tasks/scenarios); [qualified WebArena human recordings](evaluation/browser-use-public/webarena-human-suite.json), checked with the [WebArena-Verified evaluator](https://github.com/lexmount/webarena-verified/tree/lex-main) | [Curated suite](examples/trajectory_replay/README.md) · [WebArena replay](examples/trajectory_replay/public_webarena/README.md) |
| Browser Use | Let an Agent use different browsers to complete tasks | [WebArena-Verified](https://github.com/ServiceNow/webarena-verified), using the [pinned evaluator fork](https://github.com/lexmount/webarena-verified/tree/lex-main) and [declared task selection](evaluation/browser-use/webarena-verified-pilot.json) | [CowAgent + WebArena](examples/browser_use/README.md) |

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

To create and share a report, open Artifact Site ([Mainland China](https://yours.lexmount.cn/artifact-site) · [Global](https://yours.lexmount.com/artifact-site)) and install the skill offered there. Follow the skill to create and publish your report, then choose who can access it.

Reports include comparisons, task results, full responses and failure evidence. Runs are saved under `~/.browser-eval/runs/`; use `--output /absolute/path/new-run` to choose another directory.

Rebuild from saved evidence without repeating calls:

```sh
uv run --frozen browser-harness-eval report /absolute/path/to/run
```

English is the default; add `--language zh` when running or rebuilding for Chinese. A rebuild without that option keeps the saved language.

Each new report records its Browser Eval build and selected benchmark edition, with exact code and input hashes. See [versioning](docs/benchmarks.md#benchmark-editions) for comparison rules and [reference inputs](docs/reference-inputs.md) for historical data.

See the [report guide](docs/report-hosting.md) for local builds and formal benchmark releases.

## Versions

Published versions, changes and dependency snapshots are listed in [GitHub Releases](https://github.com/lexmount/browser-eval/releases). See [version rules](docs/releases.md) for release policy and report provenance.

## Related repositories

| Repository | Role |
| --- | --- |
| [LexBench Web Fetch](https://github.com/lexmount/lexbench-webfetch) | Versioned Web Fetch tasks, acceptance scoring and frozen input bindings |
| [LexBench Web Search](https://github.com/lexmount/lexbench-websearch) | Versioned search queries, source references and retrieval scoring |
| [Moli](https://github.com/lexmount/moli) | Browser evaluated through the same task contracts as other participants |
| [WebFetch CLI](https://github.com/lexmount/webfetch-cli) | CLI client for the Lexmount WebFetch service; separate from the evaluation harness |
| Artifact Site ([Mainland China](https://yours.lexmount.cn/artifact-site) · [Global](https://yours.lexmount.com/artifact-site)) | Report creation and sharing with access controls |
| [Lexbench-Headless-Browser](https://github.com/lexmount/Lexbench-Headless-Browser) | Scenario design source for curated replay; separate protocol, driver and web-platform benchmarks |
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

Project citation: [CITATION.cff](CITATION.cff).

```bibtex
@software{browser_eval_2026,
  author = {{Browser Eval contributors}},
  title = {Browser Eval},
  year = {2026},
  url = {https://github.com/lexmount/browser-eval}
}
```
