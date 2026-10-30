# LexBench-Web-Fetch

LexBench-Web-Fetch evaluates whether a browser, extraction tool or hosted fetch service returns the content an AI agent needs from a URL. Its {{total_records}} task entries cover page bodies, structured records with usable links, resource discovery and file delivery. Each task declares what the returned answer must contain, so the same acceptance rules apply to every participant.

Use it to compare live-site fetching, diagnose incomplete extraction, or test a saved response after changing a parser. [Browser Eval](https://github.com/lexmount/browser-eval) provides the scorer, execution adapters, evidence storage and comparison reports. Scoring saved answers requires no browser, provider account or LLM judge.

## Benchmark categories

The current benchmark has seven categories: general web content, dynamic websites, popular websites, access-restricted websites, embedded documents, media information and direct media files. These describe the task and its required output, rather than the directory or media format in the original catalog.

{{dataset_summary}}

The 100 general-web tasks are the baseline called “static websites” in the published comparison; that name does not mean every target is independent of JavaScript. Media-information tasks request links or page metadata; direct-media tasks require original file bytes. Images and videos can appear in either category. An access-restriction task tests delivery of its target content; a past restriction does not guarantee a challenge on every later request.

The viewer contains the 430 current task entries. Versioned definitions also preserve the [100-task article extension](definitions/evaluation/articles/tasks.json) and 20 earlier dynamic/challenge examples in the [original mixed suite](definitions/evaluation/tasks.json). Those are separate scopes and are not added to the current benchmark denominator. Historical examples have different acceptance contracts; they are retained for reproduction rather than treated as extra current samples.

## What counts as a pass

A method receives the task URL and prompt. Its response is checked against the task's declared delivery requirements:

| Delivery | Checks selected by the task |
| --- | --- |
| Body | Required facts and stable text, reference-body coverage, required regions and article endings |
| Structure | Qualifying records, names and destination URLs, fields owned by the same record, tables or document sections |
| Resources | Returned resource URLs, permitted variants, and target-only output requirements where declared |
| Files | Delivered bytes, declared file type and integrity requirements |

Contracts can combine these checks. A successful request, nonempty answer or long response alone does not establish task completion. The scorer keeps binary acceptance separate from text-quality diagnostics. Missing reference coverage remains unavailable. Exact requirements and thresholds are in each task; the [metrics guide](https://github.com/lexmount/browser-eval/blob/main/docs/metrics.md) explains their interpretation.

Web Fetch runs call the tested tools directly. An agent does not choose browser actions during execution. Preserve the requested initial-load scope: clicking, logging in, expanding hidden content or paginating changes the work unless a task explicitly permits it. In the optional article extension, a qualified displayed excerpt is scored as that excerpt, and changing feeds use their own post-collection contracts.

## A published use of the benchmark

The [Web Fetch benchmark comparison](https://browser.lexmount.com/blog/webfetch-benchmark) evaluates five browsers on these 430 task IDs under its frozen contract edition, and separately on WebMainBench's 545 saved pages. It uses the same URLs and task requirements for each browser, a 45-second task limit, and at most one retry for temporary network errors. The article reports a 96.7% request-success rate but a 54.4% task-pass rate for Chromium, illustrating how returned pages can still miss the requested content. It reports an 89.8% task-pass rate for Moli on that 430-case population.

Those results describe the article's versions, interfaces and execution conditions. They are an example of using per-task evidence to distinguish access failures from extraction failures, and are separate from scores on the current catalog. The article also explains why live fetching and extraction from fixed HTML answer different evaluation questions.

## Browse and load the data

Each viewer group has one `test` split. With the Hugging Face `datasets` library installed:

```python
import json
from datasets import load_dataset

tasks = load_dataset(
    "Lexmount/LexBench-Web-Fetch",
    "general-web",
    split="test",
    revision="<full-Hugging-Face-commit>",
)
task = json.loads(tasks[0]["task"])
print(task["url"], task["task_prompt"])
```

Replace the revision with the full dataset commit you intend to use. Select another group by its viewer name in the table above.

| Column | Meaning |
| --- | --- |
| `id` | Task identifier within its source suite |
| `input` | Source URL |
| `benchmark_category` | Task category shared with Browser Eval's report grouping |
| `source_suite` | Original scorer suite ID, retained independently of the benchmark category |
| `track` | Original source-group value from the task definition |
| `category` | Original finer category, preserved exactly as authored |
| `family` | Declared delivery kind, when present |
| `language` | Target or reference-content language, taken from source annotations or reviewed frozen references; `mul` marks multiple languages and `zxx` marks URL-only or binary targets |
| `task` | JSON serialization of the public task definition, including its prompt, acceptance requirements and external reference bindings |

The viewer supports inspection and integration. Full body references and capture metadata are external: the public row cannot replace a complete executable task that requires them.

`definitions/` contains the public definitions, catalogs and input manifests. `definitions.tar.gz` packages the same files for installation. `manifest.json` records source provenance and file hashes. Frozen catalogs retain their original dataset IDs and task languages, including historical method names inside some IDs; the same acceptance rules still apply to every method.

## Validate, score and run

From a Browser Eval checkout with [uv](https://docs.astral.sh/uv/getting-started/installation/) installed:

```sh
git clone https://github.com/lexmount/browser-eval.git
cd browser-eval
{{tooling_checkout}}uv sync --frozen
uv run --frozen python -m harness.benchmarks.data
uv run --frozen lexbench-webfetch list
uv run --frozen lexbench-webfetch validate
```

Browser Eval downloads the dataset revision selected by its [data lock](https://github.com/lexmount/browser-eval/blob/main/src/harness/benchmarks/data-lock.json) and verifies the archive and file hashes. Public downloads require no access token. The [benchmark integration guide](https://github.com/lexmount/browser-eval/blob/main/docs/benchmarks.md) covers revision selection and offline caching.

For tasks that need external reference inputs, obtain an approved matching copy and export a verified executable suite:

```sh
uv run --frozen lexbench-webfetch export \
  --suite webfetch --source /absolute/path/approved-inputs \
  --output /absolute/path/new-input-directory
```

The scorer's `--suite webfetch` exports the original mixed 150-task suite, including the 20 historical examples. To reproduce the current benchmark, select its 130 general-web, document and media task IDs plus the three current 100-task suites shown above. Select the article extension explicitly with `--suite articles`. Keep those populations separate. Export verifies the declared inputs before writing a new directory. See [reference inputs and access](https://github.com/lexmount/browser-eval/blob/main/docs/reference-inputs.md).

Score a saved response using its complete task definition:

```sh
uv run --frozen lexbench-webfetch score \
  --task /absolute/path/task.json \
  --response /absolute/path/answer.txt
```

Add `--metadata` for saved structured answer fields and `--file` for delivered file bytes. The Python API, `lexbench_webfetch.evaluation.evaluate_response`, applies the same acceptance rules.

After exporting `webfetch`, run one diagnostic task with the HTTP baseline:

```sh
uv run --frozen browser-harness-eval run \
  --dataset /absolute/path/new-input-directory/dataset.json \
  --provider-config configs/providers.quickstart.json --providers http \
  --case-ids static-simple --output /absolute/path/new-evidence
```

The HTTP baseline does not execute JavaScript. For browser setup and a report you can inspect, follow the [runnable local example](https://github.com/lexmount/browser-eval/blob/main/examples/web_fetch/README.md). Formal comparisons use the certified workflow in the [Web Fetch evaluation guide](https://github.com/lexmount/browser-eval/blob/main/src/harness/web_fetch/README.md).

## Reporting and limitations

Report passed/evaluated counts for the declared population, unavailable results and the selected benchmark and scorer revisions. Retain failed calls and timeouts. State timing boundaries, retry policy, local or remote execution, and conversion steps; keep all-call timing separate from timing conditional on success. Optional independent verification has its own disclosed scope and does not run automatically.

Live sites change with location, time, session state and access policy. The suite emphasizes task-relevant stable content, but a frozen reference can still become outdated. Investigate source drift before changing a verdict. Updating task requirements or scoring code does not retroactively update an old measurement. Record the immutable dataset commit, task/reference hashes and scorer source identity with each experiment. Detailed collection, correction and reporting rules are maintained in the [Web Fetch guide](https://github.com/lexmount/browser-eval/blob/main/src/harness/web_fetch/README.md).

These are curated workloads, not a representative sample of every website. The article suite's initial-load scope does not measure hidden continuations or interaction-dependent content. Text and link checks do not measure visual fidelity or autonomous browser use. This dataset contains task inputs and requirements; it does not ship provider measurements or establish a standing browser ranking.

## Sources, related benchmarks and rights

LexBench-Web-Fetch was developed as the Web Fetch benchmark component of [Browser Eval](https://github.com/lexmount/browser-eval). Its task definitions retain source URLs and reference bindings. Project-authored material uses Apache-2.0; original website text, documents, media and captures retain their source rights. Full captures, screenshots, private logs and measured provider outputs are excluded. A public URL or content hash is not permission to redistribute the underlying material. Read [THIRD_PARTY.md](THIRD_PARTY.md), [NOTICE](NOTICE) and the notices included with the selected snapshot.

The following projects have separate populations and terms; they are not subsets of this catalog:

- [WebMainBench](https://github.com/opendatalab/WebMainBench), maintained by OpenDataLab, evaluates main-content extraction from saved web pages. Browser Eval's [LexBench-WebMainBench dataset](https://huggingface.co/datasets/Lexmount/LexBench-WebMainBench) records its selected corpus and corrections.
- [WCXB](https://github.com/Murrough-Foley/web-content-extraction-benchmark) is the Web Content Extraction Benchmark.
- [WebArena](https://github.com/web-arena-x/webarena) and [WebArena-Verified](https://github.com/ServiceNow/webarena-verified) supply interactive tasks and evaluators used in separate browser workflows.
- [LexBench-Headless-Browser](https://huggingface.co/datasets/Lexmount/LexBench-Headless-Browser) preserves the task inputs for the [upstream Headless runtime](https://github.com/lexmount/Lexbench-Headless-Browser), which evaluates browser runtime and automation capabilities.

For search evaluation, use [LexBench-Web-Search](https://huggingface.co/datasets/Lexmount/LexBench-Web-Search). [Moli](https://github.com/lexmount/moli) is one browser that can be evaluated with Web Fetch tasks, and [WebFetch CLI](https://github.com/lexmount/webfetch-cli) is a client for a hosted fetch service. Report creation and sharing are covered by [Browser Eval's reporting documentation](https://github.com/lexmount/browser-eval/blob/main/docs/report-hosting.md).

## Citation

Cite Browser Eval and record the exact dataset revision used in your experiment. The canonical project citation is [CITATION.cff](https://github.com/lexmount/browser-eval/blob/main/CITATION.cff).

```bibtex
@software{browser_eval_2026,
  author = {{Browser Eval contributors}},
  title = {Browser Eval: Benchmarking Browsers and Web Tools for AI Agents},
  note = {Evaluation harness},
  year = {2026},
  url = {https://github.com/lexmount/browser-eval}
}
```
