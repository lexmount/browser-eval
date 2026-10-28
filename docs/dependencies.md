# Dependency sources and versions

Install the base CLI with `uv sync --frozen`. Add only the extras needed by your method.

| File | What it fixes |
| --- | --- |
| [pyproject.toml](../pyproject.toml) | Direct Python dependencies and Git sources |
| [uv.lock](../uv.lock) | Resolved Python versions and hashes |
| [package-lock.json](../package-lock.json) | Node dependency versions |

Use the declared source for each benchmark and evaluator. The full commit in the dependency lock or selected run binding is the version authority; a release tag is an optional label. See [benchmark identities](benchmarks.md#benchmark-editions). A registry package with the same name and version may contain different code.

## Core task and parsing dependencies

| Dependency | Role | Version authority |
| --- | --- | --- |
| [LexBench Web Fetch](https://github.com/lexmount/lexbench-webfetch) | Task model, catalogs, deterministic acceptance scoring and frozen input validation | Immutable Git revision in `pyproject.toml` and `uv.lock`; [benchmark integration](benchmarks.md) |
| [LexBench Web Search](https://github.com/lexmount/lexbench-websearch) | Query definitions, provenance, source qualifications and retrieval scoring | Git revision in [pyproject.toml](../pyproject.toml), [uv.lock](../uv.lock) and the [definition binding](../evaluation/lexbench-websearch.json); [Search guide](../harness/web_search/README.md) |
| [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/) | HTML parsing | `pyproject.toml` and `uv.lock` |
| [FastCDC](https://github.com/iscc/fastcdc-py) | Content chunking | `pyproject.toml` and `uv.lock` |

## Git-based runtime dependencies

| Dependency | Repository and relevant branch | Required source |
| --- | --- | --- |
| WebArena-Verified evaluator | [lexmount/webarena-verified](https://github.com/lexmount/webarena-verified) | Git dependency in [pyproject.toml](../pyproject.toml) and [uv.lock](../uv.lock); [runtime source check](../harness/trajectory_replay/webarena_runtime_source.py) |
| CowAgent Web Fetch baseline | [zhayujie/CowAgent](https://github.com/zhayujie/CowAgent) | [`ed5bb344cfe42cee443c8266ba47ee666d8d7471`](https://github.com/zhayujie/CowAgent/commit/ed5bb344cfe42cee443c8266ba47ee666d8d7471) |
| OpenClaw Web Fetch baseline | [openclaw/openclaw](https://github.com/openclaw/openclaw) | [`12c4bdf7482f24c85b011e9d858fdec3c245101c`](https://github.com/openclaw/openclaw/commit/12c4bdf7482f24c85b011e9d858fdec3c245101c) |
| DeepSeek Harness Web Fetch baseline | [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) | [`dd6322d604e00eec1ba5e0c8541159906a21094a`](https://github.com/deepseek-ai/deepseek-harness/commit/dd6322d604e00eec1ba5e0c8541159906a21094a) |

### Install the historical Web Fetch baselines

[bootstrap_oss_baselines.sh](../scripts/bootstrap_oss_baselines.sh) checks out CowAgent, OpenClaw and DeepSeek Harness at the commits above and builds their tools. It rejects uncommitted changes and unexpected repository origins. The defaults match [providers.example.json](../configs/providers.example.json) and [providers.resources.json](../configs/providers.resources.json).

| Historical runtime | Version |
| --- | --- |
| Node.js | 24.15.0 |
| pnpm | 12.1.0 |
| Python | 3.11.12 |

Read the script before running it: it downloads and builds dependencies, and replaces the checkout's `.venv`. Use a separate clone if you need to keep another environment. The Python patch pin applies to these baselines; the base CLI supports a wider range.

For a new method version, copy the provider configuration and update its pins as well as the checkout. Changing just a branch or bootstrap override fails preflight. Full commits identify the source; branch tips can move.

Browser Use records its configured clean CowAgent checkout separately. It need not use the historical Web Fetch pin. Follow the [Browser Use guide](../examples/browser_use/README.md).

## Browser binaries and library adapters

| Component | Installation source | Version authority |
| --- | --- | --- |
| Moli | [Repository](https://github.com/lexmount/moli) · [releases](https://github.com/lexmount/moli/releases) | Your selected provider config; formal cohorts also bind binary hashes |
| Chromium / Playwright | [Playwright browser installation](https://playwright.dev/python/docs/browsers) | Playwright package in uv.lock and its matching browser, or an explicit configured binary |
| Lightpanda | [lightpanda-io/browser releases](https://github.com/lightpanda-io/browser/releases) | Selected release bytes/version; a mutable nightly URL alone is insufficient for formal reproduction |
| Obscura | [h4ckf0r0day/obscura releases](https://github.com/h4ckf0r0day/obscura/releases) | Selected provider identity and cohort evidence |
| HTML to Markdown | [node-html-markdown](https://github.com/crosstype/node-html-markdown) | `package-lock.json`; adapters require 2.0.0. Install with `npm ci` |
| LangChain loader / Crawl4AI | [LangChain Community](https://github.com/langchain-ai/langchain-community) · [Crawl4AI](https://github.com/unclecode/crawl4ai) | `oss-eval`, uv.lock and the selected method's interpreter/library pins |

Prepare browser binaries, Docker images, site data, models and accounts separately. [`.env.example`](../.env.example) lists paths and credential names. `browser-harness-eval accounts` prints account setup links without making evaluation calls. Publishing uses separate [hosting credentials](report-hosting.md).

## Benchmark inputs and scorers

[WebMainBench](https://github.com/opendatalab/WebMainBench) supplies the frozen-HTML dataset and official scorer. The maintained [fork](https://github.com/lexmount/WebMainBench/tree/lex-main) contains declared scoring corrections:

| Scorer | Commit used by the full comparison |
| --- | --- |
| Official | `9d991bdc00c57b57521499494d96be85c31317ba` |
| Corrected fork | `65b49124b923e59f3cfcd839ee1af4628c36f340` |

Choose the scorer named by the experiment's [profile and correction records](../evaluation/webmainbench-full/). See the [scoring guide](../harness/web_fetch/WEBMAINBENCH.md); switching to another branch tip changes the evaluation.

| Other inputs | Setup and version record |
| --- | --- |
| [WCXB](https://github.com/Murrough-Foley/web-content-extraction-benchmark) | External run profile supplied with `--profile` |
| WebArena sites and human recordings | [Deployment and replay guide](webarena-fixed-trajectory-reproduction.md) |
| Historical source pages | Import approved copies using [reference-input validation](reference-inputs.md) |

A Git clone contains the definitions and hashes, but not all historical input bytes.

## Writing skills

Humanizer and Humanizer Chinese are not runtime dependencies. Their upstream repositories, installed commits, licenses and unchanged-file hashes are recorded in [.agents/skills](../.agents/skills/README.md). Project-owned benchmark skills hold this repository's integration requirements.
