# Dependency sources and versions

Start from `uv sync --frozen` for the base installation, or add the selected extra. [pyproject.toml](../pyproject.toml) declares direct dependencies and [uv.lock](../uv.lock) fixes resolved versions and hashes. [package-lock.json](../package-lock.json) does the same for the repository's Node dependencies. Do not substitute a similarly named registry package for a Git dependency.

## Git-based runtime dependencies

| Dependency | Repository and relevant branch | Required source |
| --- | --- | --- |
| WebArena-Verified evaluator | [lexmount/webarena-verified](https://github.com/lexmount/webarena-verified), maintained evaluator work on `lex-main` | [`da7b992eb10de78000f11a7f34fd4590c171fbd6`](https://github.com/lexmount/webarena-verified/commit/da7b992eb10de78000f11a7f34fd4590c171fbd6); installed by `browser-use-eval` from the same pin in pyproject and uv.lock |
| CowAgent Web Fetch baseline | [zhayujie/CowAgent](https://github.com/zhayujie/CowAgent) | [`ed5bb344cfe42cee443c8266ba47ee666d8d7471`](https://github.com/zhayujie/CowAgent/commit/ed5bb344cfe42cee443c8266ba47ee666d8d7471) |
| OpenClaw Web Fetch baseline | [openclaw/openclaw](https://github.com/openclaw/openclaw) | [`12c4bdf7482f24c85b011e9d858fdec3c245101c`](https://github.com/openclaw/openclaw/commit/12c4bdf7482f24c85b011e9d858fdec3c245101c) |
| DeepSeek Harness Web Fetch baseline | [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) | [`dd6322d604e00eec1ba5e0c8541159906a21094a`](https://github.com/deepseek-ai/deepseek-harness/commit/dd6322d604e00eec1ba5e0c8541159906a21094a) |

A full commit ID selects the executable bytes; a moving branch name does not. [bootstrap_oss_baselines.sh](../scripts/bootstrap_oss_baselines.sh) obtains the three Web Fetch baseline checkouts at detached commits and installs their declared toolchains. It rejects dirty checkouts and unexpected origins. Read it before running: it downloads dependencies, runs upstream builds and can take substantial time. Its default commits match [providers.example.json](../configs/providers.example.json) and [providers.resources.json](../configs/providers.resources.json). Copy new experiment configurations when intentionally changing a method version; changing only the checkout or bootstrap override will fail identity validation.

The baseline bootstrap selects Node.js 24.15.0, pnpm 12.1.0 and Python 3.11.12 to match the historical method configurations. This Python patch pin is specific to those library baselines, not the base CLI's minimum version. It replaces the checkout's `.venv` with the required interpreter; use a separate clone for baseline reproduction if you need to preserve another development environment.

The Browser Use runner accepts an explicitly configured clean CowAgent checkout and records its commit. That Agent checkout is separate from the installed WebArena evaluator and from the historical Web Fetch baseline pin. Follow its [run guide](../examples/browser_use/README.md) for the chosen model and BrowserTool contract.

## Browser binaries and library adapters

| Component | Installation source | Version authority |
| --- | --- | --- |
| Moli | [Repository](https://github.com/lexmount/moli) · [releases](https://github.com/lexmount/moli/releases) | Your selected provider config; formal cohorts also bind binary hashes |
| Chromium / Playwright | [Playwright browser installation](https://playwright.dev/python/docs/browsers) | Playwright package in uv.lock and its matching browser, or an explicit configured binary |
| Lightpanda | [lightpanda-io/browser releases](https://github.com/lightpanda-io/browser/releases) | Selected release bytes/version; a mutable nightly URL alone is insufficient for formal reproduction |
| Obscura | [h4ckf0r0day/obscura releases](https://github.com/h4ckf0r0day/obscura/releases) | Selected provider identity and cohort evidence |
| HTML to Markdown | [node-html-markdown](https://github.com/crosstype/node-html-markdown) | `package-lock.json`; adapters require 2.0.0. Install with `npm ci` |
| LangChain loader / Crawl4AI | [LangChain Community](https://github.com/langchain-ai/langchain-community) · [Crawl4AI](https://github.com/unclecode/crawl4ai) | `oss-eval`, uv.lock and the selected method's interpreter/library pins |

Docker images, application datasets, models and provider accounts are prepared separately. `.env.example` lists local paths and credential names; it does not install a browser or acquire an account. `browser-harness-eval accounts` prints provider acquisition links without making evaluation calls. Hosting credentials follow the separate [report hosting](report-hosting.md) contract.

## Benchmark inputs and scorers

[WebMainBench](https://github.com/opendatalab/WebMainBench) supplies the official frozen-HTML benchmark and scorer. Historical full-cohort profiles bind official scorer commit `9d991bdc00c57b57521499494d96be85c31317ba`. The maintained [lexmount/WebMainBench fork](https://github.com/lexmount/WebMainBench/tree/lex-main) owns declared scorer corrections; the current full-cohort correction binding names commit `65b49124b923e59f3cfcd839ee1af4628c36f340`. These are different scoring authorities, not interchangeable branch tips. Read the selected [profile and correction bindings](../evaluation/webmainbench-full/) and [scoring guide](../harness/web_fetch/WEBMAINBENCH.md) before choosing a scorer.

[WCXB](https://github.com/Murrough-Foley/web-content-extraction-benchmark) is independently bound by its [current profile](../evaluation/wcxb/current-report.json). WebArena task/site data and human recordings follow their own [deployment and replay contracts](webarena-fixed-trajectory-reproduction.md). Complete historical source inputs require approved external copies and [manifest validation](reference-inputs.md); a Git clone alone cannot reproduce those historical measurements.

## Writing skills

Humanizer and Humanizer Chinese are not runtime dependencies. Their upstream repositories, installed commits, licenses and unchanged-file hashes are recorded in [.agents/skills](../.agents/skills/README.md). Project-owned benchmark skills hold this repository's integration requirements.
