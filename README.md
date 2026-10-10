# Browser Eval

**English** | [简体中文](docs/README.zh-CN.md)

Reproducible browser benchmarks for web fetching, trajectory replay and AI agent automation. Compare task completion, latency and resource use across browsers, using shared task rules and saved evidence. Browser Eval is maintained as a companion to [Moli](https://github.com/lexmount/moli) and supports other browsers.

You get an HTML report with comparison tables, task details, responses and logs. Read it locally or publish it through [Artifact Site](https://github.com/lexmount/artifact-site) so others can explore the results online.

[What it tests](#what-it-tests) · [Quickstart](#quickstart) · [Reports](#read-and-share-a-report) · [Related projects](#related-repositories) · [Contribute](#contribute)

## What it tests

Current evaluation tracks:

| Track | Question | Start here |
| --- | --- | --- |
| Web Fetch | Can a method return the required page text, records, resources or files? How long does it take? | [Local example](examples/web_fetch/README.md) · [Full guide](harness/web_fetch/README.md) |
| Browser Trajectory Replay | Can different browsers complete the same recorded actions? What resources do they use? | [Curated suite](examples/trajectory_replay/README.md) · [WebArena recordings](examples/trajectory_replay/public_webarena/README.md) |
| Browser Use | Can an Agent complete a task when only the browser changes? | [CowAgent + WebArena guide](examples/browser_use/README.md) |

Each track reports its own task population and scores. HTTP is a fetch baseline; trajectory replay uses fixed actions; Browser Use includes Agent decisions.

<details>
<summary>Supported browsers, services and tools</summary>

| Track | Implemented participants | Preparation |
| --- | --- | --- |
| Web Fetch: local browsers | Moli, Chromium, Lightpanda, Obscura | Install the selected binary; Chromium also needs Playwright and Node.js conversion dependencies |
| Web Fetch: services | Lexmount, Firecrawl, Tavily Extract, Exa Contents, Cloudflare Markdown/Content; hosted Kitesurf via Cloudflare | Provider account and credentials in the repository `.env` |
| Web Fetch: Agent tools and extractors | CowAgent, OpenClaw, DeepSeek Harness, LangChain WebBaseLoader, Crawl4AI | Upstream checkout/runtime or the `oss-eval` extra, according to the adapter |
| Browser Trajectory Replay / Browser Use | Moli and Chromium in the current browser registry | Docker/Linux environment; Browser Use also needs CowAgent and a model account |
| Controls | Plain HTTP and offline fixture | Base Python installation |

Support varies by task type. Check [adapter requirements](docs/provider-contract.md) and [dependency versions](docs/dependencies.md) before selecting a method.

</details>

<details>
<summary>Benchmarks and datasets</summary>

| Suite | What it measures | Availability and entry |
| --- | --- | --- |
| Offline fixture / local article | Harness operation, selection, scoring and evidence; no performance ranking | Included; commands below |
| Live Web Fetch suites | Ordinary pages, dynamic content, documents, images/video, popular sites and access restrictions | [Track guide](harness/web_fetch/README.md); complete historical references require an approved [external input set](docs/reference-inputs.md) |
| WebMainBench / WCXB | Extraction fidelity on independently bound frozen HTML and reference content | [Frozen-HTML guide](harness/web_fetch/WEBMAINBENCH.md); obtain source data under its terms and use the suite's certification pipeline |
| Curated trajectory suite | Identical fixed actions on selected public pages | [Trajectory run guide](examples/trajectory_replay/README.md); uses Docker and live sites |
| WebArena human-recording replay | Qualified fixed trajectories and official task evaluation on self-hosted sites | [Reproduction guide](docs/webarena-fixed-trajectory-reproduction.md); external datasets and substantial deployment resources |
| WebArena-Verified Browser Use | Autonomous CowAgent task completion against the official evaluator | [Browser Use guide](examples/browser_use/README.md); current selection and environment are declared separately |

The Web Fetch CLI takes a dataset file. Replay and Browser Use have separate runners.

</details>

## Quickstart

Clone the repository, open a terminal in its root, and install [uv](https://docs.astral.sh/uv/getting-started/installation/). The base CLI supports Python 3.11–3.14; repository development defaults to 3.11.

```bash
uv sync --frozen
uv run --frozen browser-harness-eval validate
uv run --frozen browser-harness-eval run --delay 0
```

Open the report path printed in the terminal. This offline example needs no browser, Docker or API key. It contains one expected pass and one expected failure so you can see both kinds of result. Installation needs network access.

Next, [run HTTP, Moli or Chromium against a local article](examples/web_fetch/README.md). That guide covers browser installation, selecting several methods and choosing your own tasks. Remote services and Agent runs need their own accounts; their calls may incur charges.

<details>
<summary>Choose another Python version or install an optional adapter</summary>

To use Python 3.12, add `--python 3.12` to both `uv sync --frozen` and subsequent `uv run --frozen` commands. Optional adapters and historical experiments have additional version constraints; the full development environment uses 3.11.

| Extra | Used for |
| --- | --- |
| `headless-eval` | Chromium, PDF parsing and container measurements |
| `webmainbench-eval` | WebMainBench scoring |
| `browser-use-eval` | WebArena and Agent execution; requires Git for the pinned evaluator |
| `oss-eval` | Open-source extraction libraries |

Install an extra with `uv sync --frozen --extra NAME`. Browser binaries, Docker, site datasets and accounts are installed separately. See [dependencies](docs/dependencies.md).

</details>

## Read and share a report

| Output | What you will find |
| --- | --- |
| `report/en/index.html` | English comparisons, task results and evidence links |
| `report/index.html` | Report entry; opens the selected language |
| `results.jsonl`, `summary.json` | Scores and aggregates for scripts or further analysis |
| `manifest.json`, `dataset.json` | Methods, versions, settings and tasks used |
| `artifacts/` | Saved responses and other evidence |

Runs go to `~/.browser-eval/runs/` by default. Set `BROWSER_EVAL_DATA_HOME` to change the root, or `--output /absolute/path/new-run` for one run. Each run needs a new directory.

Rebuild a report from saved evidence without repeating browser or API calls:

```bash
uv run --frozen browser-harness-eval report /absolute/path/to/run
```

The Web Fetch CLI opens English by default, with a switchable Chinese edition. Choose Chinese when starting a run, or change the language of a saved report:

```bash
uv run --frozen browser-harness-eval run --delay 0 --language zh
uv run --frozen browser-harness-eval report /absolute/path/to/run --language en
```

Rebuilding without `--language` keeps the previous choice. Language changes do not repeat calls or change scores; task wording, responses and logs retain their original language. English (`en`) and Chinese (`zh`) are supported; other language codes are rejected. See [report language support](docs/report-hosting.md#report-languages) for the standalone builders and historical reports.

To publish online:

1. Sign in or create an account on the [China site](https://artifact-site.lexmount.cn/) or [global site](https://yours.lexmount.com/artifact-site).
2. Publish the run's `report/` directory using the [CLI or MCP guide](docs/report-hosting.md).
3. Choose who can open the report in its sharing settings.

[Artifact Site](https://github.com/lexmount/artifact-site) is open source and supports self-hosting, report versions and access controls. Local reports need no hosting account and never upload automatically.

Example runs are diagnostic. For a formal benchmark release, follow the selected track's certification and release checks; uploading a report does not certify it.

## Related repositories

| Repository | Role in this work |
| --- | --- |
| [Moli](https://github.com/lexmount/moli) | Browser evaluated alongside the other implementations |
| [Artifact Site](https://github.com/lexmount/artifact-site) | Report hosting, versions and sharing; self-hosting supported |
| [Lexbench-Headless-Browser](https://github.com/lexmount/Lexbench-Headless-Browser) | Separate benchmark for protocols, automation drivers and web-platform semantics |
| [WebArena-Verified fork](https://github.com/lexmount/webarena-verified/tree/lex-main) | Tasks and evaluator for Browser Use and public WebArena replay; maintained on `lex-main` |
| [WebMainBench fork](https://github.com/lexmount/WebMainBench/tree/lex-main) · [upstream](https://github.com/opendatalab/WebMainBench) | Frozen-HTML extraction benchmark; the fork maintains declared scoring corrections |
| [WCXB](https://github.com/Murrough-Foley/web-content-extraction-benchmark) | Independent content-extraction benchmark |
| [CowAgent](https://github.com/zhayujie/CowAgent) | Browser Use Agent runner and Web Fetch tool baseline |
| [OpenClaw](https://github.com/openclaw/openclaw) · [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | Web Fetch tool baselines |

See [dependency sources and pins](docs/dependencies.md) for exact versions. Install only the components your selected method needs.

## Contribute

PRs are welcome for adapters, tasks, tests, scoring fixes, documentation and translations. Start with [CONTRIBUTING](CONTRIBUTING.md), or [open an issue](https://github.com/lexmount/browser-eval/issues) with a reproducible example.

```bash
make setup-test
make ci
```

Historical pages and measured outputs live outside Git. For reproducing past runs, see [reference inputs](docs/reference-inputs.md). For code layout and release internals, see [architecture](docs/architecture.md).

Project code is [MIT licensed](LICENSE). [THIRD_PARTY](THIRD_PARTY.md) covers data rights and attribution; [SECURITY](SECURITY.md) covers credentials and untrusted content.

## Cite Browser Eval

If you use the harness, task definitions or evaluation methodology in published work, cite the repository and record the tag or commit used. GitHub's citation menu reads [CITATION.cff](CITATION.cff). For the initial release:

```bibtex
@software{browser_eval_2026,
  author = {{Browser Eval contributors}},
  title = {Browser Eval},
  year = {2026},
  version = {0.1.0},
  url = {https://github.com/lexmount/browser-eval}
}
```

Cite upstream benchmarks and evaluators separately when using their data or methods; this repository citation does not replace their attribution requirements.
