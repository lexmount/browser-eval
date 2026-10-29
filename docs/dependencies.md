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
| Web Fetch scoring (`src/lexbench_webfetch/`) | Task model, deterministic acceptance and frozen-input validation | Browser Eval source revision; [HF definitions](https://huggingface.co/datasets/Lexmount/lexbench-webfetch) selected by the [data lock](../src/harness/benchmarks/data-lock.json) |
| Search scoring (`src/lexbench_websearch/`) | Source qualifications and retrieval/relevance metrics | Browser Eval source revision; [Search](https://huggingface.co/datasets/Lexmount/lexbench-websearch) and [SimpleQA](https://huggingface.co/datasets/Lexmount/lexbench-simpleqa-verified) definitions selected by the [data lock](../src/harness/benchmarks/data-lock.json) |
| [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/) | HTML parsing | `pyproject.toml` and `uv.lock` |
| [FastCDC](https://github.com/iscc/fastcdc-py) | Content chunking | `pyproject.toml` and `uv.lock` |

## Git-based runtime dependencies

| Dependency | Repository and relevant branch | Required source |
| --- | --- | --- |
| WebArena-Verified evaluator | [lexmount/webarena-verified](https://github.com/lexmount/webarena-verified) | Git dependency in [pyproject.toml](../pyproject.toml) and [uv.lock](../uv.lock); [runtime source check](../src/harness/trajectory_replay/webarena_runtime_source.py) |
| CowAgent Web Fetch baseline | [zhayujie/CowAgent](https://github.com/zhayujie/CowAgent) | `cowagent_rev` in [bootstrap_oss_baselines.sh](../scripts/bootstrap_oss_baselines.sh); configured `expected_repo_revision` |
| OpenClaw Web Fetch baseline | [openclaw/openclaw](https://github.com/openclaw/openclaw) | `openclaw_rev` in [bootstrap_oss_baselines.sh](../scripts/bootstrap_oss_baselines.sh); configured `expected_repo_revision` |
| DeepSeek Harness Web Fetch baseline | [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) | `dsh_rev` in [bootstrap_oss_baselines.sh](../scripts/bootstrap_oss_baselines.sh); configured `expected_repo_revision` |

### Install the historical Web Fetch baselines

[bootstrap_oss_baselines.sh](../scripts/bootstrap_oss_baselines.sh) checks out CowAgent, OpenClaw and DeepSeek Harness at its declared commits and builds their tools. It rejects uncommitted changes and unexpected repository origins. The defaults match [providers.example.json](../configs/providers.example.json) and [providers.resources.json](../configs/providers.resources.json).

Node.js, pnpm and Python installation versions are declared in that script. The
selected provider configuration checks them through its `expected_*_version`
fields.

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
| HTML to Markdown | [node-html-markdown](https://github.com/crosstype/node-html-markdown) | [package-lock.json](../package-lock.json); install with `npm ci` |
| LangChain loader / Crawl4AI | [LangChain Community](https://github.com/langchain-ai/langchain-community) · [Crawl4AI](https://github.com/unclecode/crawl4ai) | `oss-eval`, uv.lock and the selected method's interpreter/library pins |

Prepare browser binaries, Docker images, site data, models and accounts separately. [`.env.example`](../.env.example) lists paths and credential names. `browser-harness-eval accounts` prints account setup links without making evaluation calls. Publishing uses separate [hosting credentials](report-hosting.md).

## Benchmark inputs and scorers

The [corrected WebMainBench dataset](https://huggingface.co/datasets/Lexmount/lexbench-webmainbench) supplies frozen HTML and references through the data lock. [OpenDataLab WebMainBench](https://github.com/opendatalab/WebMainBench) is the upstream source; its maintained [scorer fork](https://github.com/lexmount/WebMainBench/tree/lex-main) contains declared scoring corrections:

| Scorer | Version authority |
| --- | --- |
| Official default | `SCORER_COMMIT` in [webmainbench_scoring.py](../src/harness/web_fetch/webmainbench_scoring.py) |
| Selected correction | `scorer_commit` in the selected experiment's correction binding; [existing binding](../evaluation/webmainbench-full/scoring-correction-binding.json) |

Choose the scorer named by the experiment's [profile and correction records](../evaluation/webmainbench-full). See the [scoring guide](../src/harness/web_fetch/WEBMAINBENCH.md); switching to another branch tip changes the evaluation.

| Other inputs | Setup and version record |
| --- | --- |
| [Qualified WCXB inputs](https://huggingface.co/datasets/Lexmount/lexbench-wcxb) | Data lock and selected source manifest; runtime/scoring in Browser Eval |
| [Curated replay definitions](https://huggingface.co/datasets/Lexmount/lexbench-trajectory-replay) | Data lock and selected task/trajectory hashes |
| [WebArena replay tasks and recordings](https://huggingface.co/datasets/Lexmount/lexbench-webarena-replay) | Data lock and selected configuration; the pinned official evaluator remains a code dependency |
| WebArena sites | [Deployment and replay guide](webarena-fixed-trajectory-reproduction.md) |
| Historical source pages | Import approved copies using [reference-input validation](reference-inputs.md) |

The Browser Eval checkout contains code and input bindings. Current public definitions are downloaded from the selected HF editions; complete historical input copies may require separate acquisition.

## Writing skills

Humanizer and Humanizer Chinese are not runtime dependencies. Their upstream repositories, installed commits, licenses and unchanged-file hashes are recorded in [.agents/skills](../.agents/skills/README.md). Project-owned benchmark skills hold this repository's integration requirements.
