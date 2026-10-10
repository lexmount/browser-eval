# Browser Eval

**English** | [简体中文](docs/README.zh-CN.md)

Reproducible browser evaluations, maintained as a companion to [Moli](https://github.com/lexmount/moli). Compare browsers, web-fetch services and Agent tools using declared tasks, shared acceptance rules and inspectable evidence.

Turn a run into a browsable HTML report: compare results, inspect individual tasks and follow links to saved responses and logs. Publish it to [Artifact Site](https://github.com/lexmount/artifact-site) for online reading and sharing, or keep it local.

[Capabilities](#what-you-can-evaluate) · [Quickstart](#start-with-the-offline-example) · [Publish a report](#publish-and-share-a-report) · [Related repositories](#related-repositories) · [Contribute](#contribute-and-reproduce)

## What you can evaluate

| Track | Inputs and evaluation | Outputs | Guide |
| --- | --- | --- | --- |
| Web Fetch | Live URLs or frozen HTML; body coverage, structured records, resource discovery and file delivery | Task verdicts, content metrics, latency, raw responses and HTML report; container resource metrics when configured | [Web Fetch](harness/web_fetch/README.md) |
| Browser Trajectory Replay | Fixed BrowserTool action trajectories; completion and resource use across browsers | Action responses, evaluator results, timings, resource measurements and report | [Local suite](examples/trajectory_replay/README.md) · [WebArena recordings](examples/trajectory_replay/public_webarena/README.md) |
| Browser Use | Autonomous tasks with the same Agent, model and tool contract; official WebArena-Verified evaluation | Task scores, model usage, trajectories, logs and report | [Agent run guide](examples/browser_use/README.md) |

Each track owns its tasks, denominator and publication target. Their pass rates do not combine into one browser score. HTTP fetching is a baseline, not browser automation; fixed trajectory replay does not evaluate autonomous planning.

### Browsers and methods

| Track | Implemented participants | Preparation |
| --- | --- | --- |
| Web Fetch: local browsers | Moli, Chromium, Lightpanda, Obscura | Install the selected binary; Chromium also needs Playwright and Node.js conversion dependencies |
| Web Fetch: services | Lexmount, Firecrawl, Tavily Extract, Exa Contents, Cloudflare Markdown/Content; hosted Kitesurf via Cloudflare | Provider account and credentials in the repository `.env` |
| Web Fetch: Agent tools and extractors | CowAgent, OpenClaw, DeepSeek Harness, LangChain WebBaseLoader, Crawl4AI | Upstream checkout/runtime or the `oss-eval` extra, according to the adapter |
| Browser Trajectory Replay / Browser Use | Moli and Chromium in the current browser registry | Docker/Linux environment; Browser Use also needs CowAgent and a model account |
| Controls | Plain HTTP and offline fixture | Base Python installation |

An adapter's presence does not mean it supports every task kind or benchmark. Select its declared output contract and prerequisites. See [dependency sources and pinned versions](docs/dependencies.md), [provider configuration](configs/providers.example.json), [native browser routes](harness/web_fetch/HEADLESS.md) and the [adapter contract](docs/provider-contract.md). Those full configurations retain historical version pins; use the quickstart below for a new local run.

### Benchmarks and task suites

| Suite | What it measures | Availability and entry |
| --- | --- | --- |
| Offline fixture / local article | Harness operation, selection, scoring and evidence; no performance ranking | Included; commands below |
| Live Web Fetch suites | Ordinary pages, dynamic content, documents, images/video, popular sites and access restrictions | [Track guide](harness/web_fetch/README.md); complete historical references require an approved [external input set](docs/reference-inputs.md) |
| WebMainBench / WCXB | Extraction fidelity on independently bound frozen HTML and reference content | [Frozen-HTML guide](harness/web_fetch/WEBMAINBENCH.md); obtain source data under its terms and use the suite's certification pipeline |
| Curated trajectory suite | Identical fixed actions on selected public pages | [Trajectory run guide](examples/trajectory_replay/README.md); uses Docker and live sites |
| WebArena human-recording replay | Qualified fixed trajectories and official task evaluation on self-hosted sites | [Reproduction guide](docs/webarena-fixed-trajectory-reproduction.md); external datasets and substantial deployment resources |
| WebArena-Verified Browser Use | Autonomous CowAgent task completion against the official evaluator | [Browser Use guide](examples/browser_use/README.md); current selection and environment are declared separately |

The general CLI selects a Web Fetch dataset file, not a benchmark name. Replay and autonomous runs have their own entry points; they are not options to `browser-harness-eval run`.

## Start with the offline example

Clone this repository and run commands from its root. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.11 or newer. The base CLI is tested on Python 3.11 through 3.14; `.python-version` selects 3.11 for repository development and frozen experiments. To choose 3.12, add `--python 3.12` to both `uv sync --frozen` and subsequent `uv run --frozen` commands. Optional dependencies and historical methods have their own runtime constraints; the full development/reproduction environment remains pinned to 3.11.

```bash
uv sync --frozen
uv run --frozen browser-harness-eval validate
uv run --frozen browser-harness-eval run --delay 0
```

Initial installation needs network access. The example uses no websites, Docker or API keys and contains one declared success and one declared failure. Open the absolute report path printed in the terminal. Adding `--fail-on-failure` returns exit code 2 for this example.

## Run a real adapter against a local page

This example serves a repository-owned article and evaluates its complete text. It requires no external dataset or account. Keep this command running in a separate terminal:

```bash
uv run --frozen python -m http.server 8765 --bind 127.0.0.1 \
  --directory examples/web_fetch/site
```

In another terminal at the repository root, validate and run the HTTP baseline:

```bash
uv run --frozen browser-harness-eval validate \
  --dataset examples/web_fetch/dataset.json \
  --provider-config configs/providers.quickstart.json --providers http
uv run --frozen browser-harness-eval run \
  --dataset examples/web_fetch/dataset.json \
  --provider-config configs/providers.quickstart.json --providers http --delay 0
```

The intact article should pass. `http` parses response text without executing JavaScript. To run Moli, install it from the [Moli releases](https://github.com/lexmount/moli/releases), put `moli` on PATH or set `MOLI_BINARY` to its absolute path, and replace `--providers http` with `--providers moli` in both commands. Select several installed methods with `--providers http,moli,chromium`; Lightpanda and Obscura are also available in the [quickstart configuration](configs/providers.quickstart.json). Only selected methods are initialized. This static article checks integration, not differences in dynamic-page capability.

Chromium needs these additional dependencies:

```bash
uv sync --frozen --extra headless-eval
uv run --frozen playwright install chromium
npm ci
```

Node.js 22 is the repository's tested Node runtime. `CHROMIUM_BINARY` may select an explicit binary; otherwise the adapter can use Playwright's installed Chromium. Other binary variables are `LIGHTPANDA_BINARY` and `OBSCURA_BINARY`. Quickstart configurations record actual identities without enforcing historical pins; formal comparisons must declare and validate intended versions before calls. Local HTTP access is intentional in this example; keep its server bound to loopback.

### Read and rebuild the output

Each run prints the absolute report path; the report lives inside that run's evidence directory. Output defaults to `~/.browser-eval/runs/`; set `BROWSER_EVAL_DATA_HOME` to change the root or `--output /absolute/path/new-run` to select a new directory. Existing evidence directories are rejected.

| File or directory | Contents |
| --- | --- |
| `report/index.html` | Summary, task inventory and links to result/evidence pages |
| `results.jsonl`, `summary.json` | Machine-readable per-attempt scores and aggregates |
| `manifest.json`, `dataset.json` | Run configuration, participant identities and selected tasks |
| `artifacts/` | Saved provider output and associated evidence |

Rebuild the HTML from saved evidence without making new provider calls:

```bash
uv run --frozen browser-harness-eval report /absolute/path/to/run
```

Report builders default to Chinese. English generation is an explicit option in supported track builders; when absent, the English entry displays a notice with a return link. Repository documentation is English-first, with this Chinese README and original-language frozen evidence.

## Select your own tasks and comparison

Copy the local dataset and provider configuration, declare independent references and acceptance rules, and set each task's URL. Pass both files explicitly:

```bash
uv run --frozen browser-harness-eval run \
  --dataset /absolute/path/dataset.json \
  --provider-config /absolute/path/providers.json --providers method-a,method-b \
  --case-ids case-a,case-b --task-kind body \
  --output /absolute/path/new-evidence
```

`--providers` contains configuration keys, not arbitrary browser executable names. `--case-ids` and `--task-kind` intersect; other task kinds are `structure`, `resource` and `file`. The report includes only selected tasks and methods. Consult [metrics](docs/metrics.md) and the [adapter contract](docs/provider-contract.md) before interpreting acceptance or adding a method.

Ordinary CLI runs produce diagnostic evidence. Formal Web Fetch cohorts use `scripts/run_certified_benchmark.py` and their track-specific configuration to freeze and recheck references; partial or uncertified runs cannot become formal benchmark releases. Model and remote API calls may incur costs. Historical publication profiles are reproduction authorities, not ready-made targets for your own experiment.

Install extras only as needed: `headless-eval` for local Chromium/PDF/container support, `webmainbench-eval` for WebMainBench scoring, `browser-use-eval` for WebArena/Agent execution, and `oss-eval` for open-source extractors. `browser-use-eval` requires Git and installs the pinned WebArena-Verified fork; a registry package with the same version cannot replace that evaluator. Browser binaries, Docker, site deployments and accounts remain separate installations.

## Publish and share a report

Reports include comparison tables, task results and links to the saved evidence. [Artifact Site](https://github.com/lexmount/artifact-site) is our open-source hosting project for these multi-page reports, with version history and sharing controls. You can use a hosted instance or deploy it yourself.

1. Run an example above and open the generated `report/index.html` locally.
2. Choose the [China site](https://artifact-site.lexmount.cn/) or [global site](https://yours.lexmount.com/artifact-site), then sign in or create an account to keep and manage your reports.
3. Publish the run's `report/` directory through the CLI or MCP, then choose who can open it in the sharing controls. The [hosting guide](docs/report-hosting.md) gives commands for both sites and explains updates and access settings.

Local reports need no account and never upload automatically. Publishing an example preserves its diagnostic status; formal benchmark releases use their own certification and publication contracts. Use your own account and report target.

## Related repositories

| Repository | Role in this work |
| --- | --- |
| [Moli](https://github.com/lexmount/moli) | Browser implementation; Browser Eval compares it with other browsers under shared task and acceptance rules |
| [Artifact Site](https://github.com/lexmount/artifact-site) | Optional report hosting, versions and sharing; also available for self-hosting |
| [Lexbench-Headless-Browser](https://github.com/lexmount/Lexbench-Headless-Browser) | Companion benchmark for browser protocols, automation drivers and web-platform semantics; its suite and scores are separate from this repository's tracks |
| [WebArena-Verified fork](https://github.com/lexmount/webarena-verified/tree/lex-main) | Pinned task definitions and evaluator used by Browser Use and public WebArena replay; maintained integration and evaluator work lives on `lex-main` |
| [WebMainBench fork](https://github.com/lexmount/WebMainBench/tree/lex-main) · [upstream](https://github.com/opendatalab/WebMainBench) | Frozen-HTML main-content benchmark; the fork maintains declared scorer corrections, with each experiment binding its scoring authority |
| [WCXB](https://github.com/Murrough-Foley/web-content-extraction-benchmark) | Independent web-content extraction benchmark used by the WCXB report track |
| [CowAgent](https://github.com/zhayujie/CowAgent) | Agent runner for Browser Use and a Web Fetch tool baseline |
| [OpenClaw](https://github.com/openclaw/openclaw) · [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | Additional Web Fetch tool baselines |

Install only what your selected track and method need. [Dependency sources and versions](docs/dependencies.md) records exact commits, runtime requirements and the other browser/library adapters; [THIRD_PARTY](THIRD_PARTY.md) covers attribution and licenses.

## Contribute and reproduce

PRs are welcome for adapters, tasks, regression tests, scoring fixes, documentation and README translations. Apply fixes through shared contracts and use the same acceptance/evidence requirements for every browser. Read [CONTRIBUTING](CONTRIBUTING.md), or [open an issue](https://github.com/lexmount/browser-eval/issues) with a reproducible example. Moli installation and browser usage are documented in the [Moli repository](https://github.com/lexmount/moli).

```bash
make setup-test
make ci
```

The full gate uses the pinned Python environment, Node.js, native protocol tests and independent package installation. Base compatibility checks also exercise the public CLI on Python 3.12, 3.13 and 3.14. Real container and account-based experiments need their declared environments.

Git contains reusable code, compact definitions, acceptance contracts and evidence bindings. Complete historical inputs are external and are not downloaded by tests; import approved copies through the [reference-input guide](docs/reference-inputs.md). `make test-reference-inputs` fails when required sets are missing or corrupt. Provider outputs, execution manifests, authored reports, screenshots and publication bundles stay outside Git.

Project-owned code uses [MIT](LICENSE). Third-party data rights and vendored skill sources are described in [THIRD_PARTY](THIRD_PARTY.md); see [SECURITY](SECURITY.md) for credentials and untrusted-content handling.

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
