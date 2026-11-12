# LexBench-Web-Fetch

Short name: **LB-WF**.

LexBench-Web-Fetch evaluates whether a browser, extraction tool or hosted fetch service returns the content an AI agent needs from a URL. Its 530 task entries cover page bodies, structured records with usable links, resource discovery and file delivery. Each task declares what the returned answer must contain, so the same acceptance rules apply to every participant.

Use it to compare live-site fetching, diagnose incomplete extraction, or test a saved response after changing a parser. [Webstone](https://github.com/lexmount/webstone) provides the scorer, execution adapters, evidence storage and comparison reports. Scoring saved answers requires no browser, provider account or LLM judge.

**License.** The `apache-2.0` license covers the project-authored code, documentation and task requirements. The reference texts included for evaluation under `definitions/materials/` are third-party content. Apache-2.0 does not cover them, and they keep their source terms; see [THIRD_PARTY.md](THIRD_PARTY.md).


## Dataset versions

This snapshot is **v3.0.0: 530 tasks**. See [version history and comparison rules](VERSIONING.md) for the 530-task edition, the historical 550-task (v2.x) and 430-task (v1.0.0) editions, version increments and immutable revision selection. Report v3.0.0 results separately from v2.x. The active population is recorded in `manifest.json` under `edition`; retained historical definitions do not increase that population.

## 530-task revision

`primary-content-20261103` is one cumulative task-scope revision for a 530-task
population. It changes 72 cases against the previous revision: 64 scope corrections,
article-009 and article-064, three more Xueqiu articles, and binding-only repairs
for live-041, live-063 and live-069. The six Xueqiu tasks use one declared shell rule
(`xueqiu-shell-v1`), and the two Quanta tasks use another (`quanta-shell-v1`).
All other case rules and previously released versions remain unchanged. A single
compact rationale file explains this rule set; it is not an alternative version.

The revision is intended for **formal evaluations under a separate major task
contract**, using the fixed 530-task selection and fresh matched runs after adoption.
It does not certify the old report or establish that all similar requirements in
the catalog have been corrected. The 66 earlier cases have source-context review
records, which disclose partial observations and unavailable historical captures.
The three later Xueqiu cases have no full-page capture among the bound inputs (see
the review notes).
Review here means assistant-led source inspection and automated checks, not
independent human approval. Raw captures are external, so these checks cannot be
replayed using the public repository alone. Independent sampling of the removals
is still needed before formal adoption. Each case's full-answer control, counterfactual
control and source hashes are recorded under `cases[]` in the rationale file.
Freeze the rules and inputs before collecting outputs. Saved-answer rescoring is
reported separately as sensitivity analysis. See the [scope and adoption requirements](definitions/annotation-revisions/primary-content-20261103.md).

### 530-task selection

The selection retains the earlier 430-task edition and adds all 100 article
tasks. It is the active v3.0.0 edition. The 550-task catalog also counted two
10-task example groups from the mixed webfetch suite:

- The 10 dynamic examples have the same IDs and URLs as tasks in the dedicated
  100-task dynamic-content suite. Counting both would count those targets twice.
  Their acceptance contracts differ; the dynamic-content contracts are the ones
  selected.
- The 10 challenge examples (Amazon, JD, Xiaohongshu, Zhihu, Douban, Dianping,
  Reddit, IMDb, Anthropologie and JustWatch list pages) are an example group.
  They stay available as a separate challenge set and are not counted in the
  edition. Only three share a URL with another selected task.

The selection file records this policy. This selection was documented after outcomes were available; it is a rule-based cohort boundary, not a pre-outcome selection protocol or a representative sample. Historical editions are unchanged.

The revision uses the explicit [530-task selection](definitions/populations/primary-content-20261103.json): 130 webfetch tasks and 100 each from dynamic-content, popular-sites, access-restrictions and articles. The excluded 20 webfetch entries remain in the historical frozen files. Matching dynamic-content entries remain selected under their own suite identity.

After exporting a suite with the compatible exporter and `--revision primary-content-20261103`, generate its runner selection from this dataset checkout:

```sh
uv run --frozen python tools/task_population.py --suite webfetch
```

With the compatible Webstone population support, prefer
`--population /path/to/definitions/populations/primary-content-20261103.json --population-suite webfetch`
on the certified runner, using a dataset exported with this revision. Repeat for
each of the five suites. The runner checks the selection against the exported
rule hash and frozen suite identity before calls, then records the population
hash, selected IDs and rule revision in the result. A single-suite run is not a
complete 530-task evaluation. The helper above remains available for legacy
manual `--case-ids` selection, which does not provide this population binding.
The plain webfetch export remains unchanged.

## Benchmark categories

The earlier 430-task edition has seven categories: general web content, dynamic websites, popular websites, access-restricted websites, embedded documents, media information and direct media files. The v3.0.0 edition adds article tasks for 530 tasks in eight viewer groups. Categories describe the task and its required output.

{{dataset_summary}}

The `challenge-examples` viewer group (10 `webfetch` access-challenge list pages) is a separate challenge set. It is **not counted** in the 530-task edition; report its results separately.

The 100 general-web tasks are the baseline called “static websites” in the published comparison; that name does not mean every target is independent of JavaScript. Media-information tasks request links or page metadata; direct-media tasks require original file bytes. Images and videos can appear in either category. An access-restriction task tests delivery of its target content; a past restriction does not guarantee a challenge on every later request.

The table above identifies this edition's active population. The historical 550-task (v2.x) edition also counted 20 dynamic/challenge examples from the [original mixed suite](definitions/evaluation/tasks.json); the 430-task edition excludes the [100 article tasks](definitions/evaluation/articles/tasks.json) and those examples. The v2.x viewer rows remain available at the v2.2.0 tag. Definitions retained for reproduction do not add samples to an edition unless its viewer and population manifest select them.

## What counts as a pass

A method receives the task URL and prompt. Its response is checked against the task's declared delivery requirements:

| Delivery | Checks selected by the task |
| --- | --- |
| Body | Required facts and stable text, reference-body coverage, required regions and article endings |
| Structure | Qualifying records, names and destination URLs, fields owned by the same record, tables or document sections |
| Resources | Returned resource URLs, permitted variants, and target-only output requirements where declared |
| Files | Delivered bytes, declared file type and integrity requirements |

Contracts can combine these checks. A successful request, nonempty answer or long response alone does not establish task completion. The scorer keeps binary acceptance separate from text-quality diagnostics. Missing reference coverage remains unavailable. Exact requirements and thresholds are in each task; the [metrics guide](https://github.com/lexmount/webstone/blob/main/docs/metrics.md) explains their interpretation.

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
| `benchmark_category` | Task category shared with Webstone's report grouping |
| `source_suite` | Original scorer suite ID, retained independently of the benchmark category |
| `track` | Original source-group value from the task definition |
| `category` | Original finer category, preserved exactly as authored |
| `family` | Declared delivery kind, when present |
| `language` | Target or reference-content language, taken from source annotations or reviewed frozen references; `mul` marks multiple languages and `zxx` marks URL-only or binary targets |
| `task` | JSON serialization of the public task definition, including its prompt, acceptance requirements and reference-text hash bindings |

The viewer supports inspection and integration. Viewer rows show hashes in place of reference texts. For the 530-task revision, the scorable task records with their reference texts are in `definitions/materials/primary-content-20261103/` (see below).

`definitions/` contains the public definitions, catalogs and input manifests. `definitions.tar.gz` packages the same files for installation. `manifest.json` records source provenance and file hashes. Frozen catalogs retain their original dataset IDs and task languages, including historical method names inside some IDs; the same acceptance rules still apply to every method.

## Validate, score and run

From a Webstone checkout with [uv](https://docs.astral.sh/uv/getting-started/installation/) installed:

```sh
git clone https://github.com/lexmount/webstone.git
cd webstone
{{tooling_checkout}}uv sync --frozen
uv run --frozen python -m harness.benchmarks.data
uv run --frozen lexbench-webfetch list
uv run --frozen lexbench-webfetch validate
```

Webstone downloads the dataset revision selected by its [data lock](https://github.com/lexmount/webstone/blob/main/src/harness/benchmarks/data-lock.json) and verifies the archive and file hashes. Public downloads require no access token. The [benchmark integration guide](https://github.com/lexmount/webstone/blob/main/docs/benchmarks.md) covers revision selection and offline caching.

### Score the 530-task revision from this download

`definitions/materials/primary-content-20261103/<suite>/dataset.json` contains the
complete scorable tasks, with `primary-content-20261103` already applied, for
`webfetch`, `dynamic-content`, `popular-sites`, `access-restrictions` and `articles`.
Together they cover all 530 selected tasks. Each directory's `source-inputs.json` and
`dataset-catalog.json` bind the file by SHA-256. The selection's
`frozen_task_bindings[<suite>].exported_cases_sha256` binds its resolved cases.

Each file is the frozen dataset bound by the suite's `definitions/evaluation/.../source-inputs.json`.
Its hashes were verified, and it reproduces every public task row. The Webstone
revision code then applied `primary-content-20261103`, and the result is serialized
the way `export` writes it. These files are ready to score as published, with the
revision already applied. Re-running `lexbench-webfetch export --revision
primary-content-20261103` needs each suite's private frozen parent inputs, including
source trees that are not in this download. Re-applying the revision also needs Webstone at
[`351cb27b`](https://github.com/lexmount/webstone/commit/351cb27b1d9bf82ae81b177874ea5e624ecb00f0)
or later, because its review inherits unchanged entries from `review-550-20261023`.
Install a suite with Webstone's existing importer, then
run it with the population:

```sh
M=/path/to/definitions/materials/primary-content-20261103/webfetch
uv run --frozen python scripts/import_reference_inputs.py --manifest "$M/source-inputs.json" --source "$M"
uv run --frozen python scripts/run_certified_benchmark.py --dataset "$M/dataset.json" \
  --population /path/to/definitions/populations/primary-content-20261103.json \
  --population-suite webfetch --retrieval-mode native ...
```

To score one saved answer, write a task object from that `dataset.json` to a file.
Declare the answer representation in a metadata file, because text without a declared
representation is read as markup:

```sh
uv run --frozen python -c 'import json,sys; d=json.load(open(sys.argv[1])); json.dump(next(c for c in d["cases"] if c["id"]=="static-simple"), open("task.json","w"), ensure_ascii=False)' "$M/dataset.json"
echo '{"answer_format": "plain_text"}' > metadata.json
uv run --frozen lexbench-webfetch score --task task.json --response answer.txt --metadata metadata.json
```

Scoring uses only the task object. The
declared-retrieval route and the 20 excluded webfetch examples also need the frozen
`sources` and `list-references` trees, which this download does not include.

### Historical editions

For tasks that need external reference inputs, obtain an approved matching copy and export a verified executable suite:

```sh
uv run --frozen lexbench-webfetch export \
  --suite webfetch --source /absolute/path/approved-inputs \
  --output /absolute/path/new-input-directory
```

The scorer's `--suite webfetch` exports the mixed 150-task suite, including 20 dynamic/challenge examples. For the 430-task edition, select its 130 general-web, document and media IDs plus the three 100-task suites. For the historical 550-task (v2.x) edition, select all 150 mixed tasks, those three suites and the 100 article tasks (`--suite articles`). Export verifies inputs before writing a new directory. See [reference inputs and access](https://github.com/lexmount/webstone/blob/main/docs/reference-inputs.md).

Score a saved response using its complete task definition:

```sh
uv run --frozen lexbench-webfetch score \
  --task /absolute/path/task.json \
  --response /absolute/path/answer.txt
```

Add `--metadata` for saved structured answer fields and `--file` for delivered file bytes. The Python API, `lexbench_webfetch.evaluation.evaluate_response`, applies the same acceptance rules.

After exporting `webfetch`, run one diagnostic task with the HTTP baseline:

```sh
uv run --frozen webstone run \
  --dataset /absolute/path/new-input-directory/dataset.json \
  --provider-config configs/providers.quickstart.json --providers http \
  --case-ids static-simple --output /absolute/path/new-evidence
```

The HTTP baseline does not execute JavaScript. For browser setup and a report you can inspect, follow the [runnable local example](https://github.com/lexmount/webstone/blob/main/examples/web_fetch/README.md). Formal comparisons use the certified workflow in the [Web Fetch evaluation guide](https://github.com/lexmount/webstone/blob/main/src/harness/web_fetch/README.md).

## Reporting and limitations

Report passed/evaluated counts for the declared population, unavailable results and the selected benchmark and scorer revisions. Retain failed calls and timeouts. State timing boundaries, retry policy, local or remote execution, and conversion steps; keep all-call timing separate from timing conditional on success. Optional independent verification has its own disclosed scope and does not run automatically.

Live sites change with location, time, session state and access policy. The suite emphasizes task-relevant stable content, but a frozen reference can still become outdated. Investigate source drift before changing a verdict. Updating task requirements or scoring code does not retroactively update an old measurement. Record the immutable dataset commit, task/reference hashes and scorer source identity with each experiment. Detailed collection, correction and reporting rules are maintained in the [Web Fetch guide](https://github.com/lexmount/webstone/blob/main/src/harness/web_fetch/README.md).

These are curated workloads, not a representative sample of every website. The article suite's initial-load scope does not measure hidden continuations or interaction-dependent content. Text and link checks do not measure visual fidelity or autonomous browser use. This dataset contains task inputs and requirements; it does not ship provider measurements or establish a standing browser ranking.

## Sources, related benchmarks and rights

LexBench-Web-Fetch was developed as the Web Fetch benchmark component of [Webstone](https://github.com/lexmount/webstone). Its task definitions retain source URLs and reference bindings. Project-authored material uses Apache-2.0. The reference texts included for evaluation keep their original language and their source terms, and the Apache-2.0 license does not cover them. To request removal or correction, open a discussion in this repository's Community tab. Full captures, screenshots, document and media files, private logs and measured provider outputs are excluded. A public URL or content hash is not permission to redistribute the underlying material. Read [THIRD_PARTY.md](THIRD_PARTY.md), [NOTICE](NOTICE) and the notices included with the selected snapshot.

The following projects have separate populations and terms; they are not subsets of this catalog:

- [WebMainBench](https://github.com/opendatalab/WebMainBench), maintained by OpenDataLab, evaluates main-content extraction from saved web pages. Webstone's [LexBench-WebMainBench dataset](https://huggingface.co/datasets/Lexmount/LexBench-WebMainBench) records its selected corpus and corrections.
- [WCXB](https://github.com/Murrough-Foley/web-content-extraction-benchmark) is the Web Content Extraction Benchmark.
- [WebArena](https://github.com/web-arena-x/webarena) and [WebArena-Verified](https://github.com/ServiceNow/webarena-verified) supply interactive tasks and evaluators used in separate browser workflows.
- [LexBench-Headless-Browser](https://huggingface.co/datasets/Lexmount/LexBench-Headless-Browser) preserves the task inputs for the [upstream Headless runtime](https://github.com/lexmount/Lexbench-Headless-Browser), which evaluates browser runtime and automation capabilities.

For search evaluation, use [LexBench-Web-Search](https://huggingface.co/datasets/Lexmount/LexBench-Web-Search). [Moli](https://github.com/lexmount/moli) is one browser that can be evaluated with Web Fetch tasks, and [WebFetch CLI](https://github.com/lexmount/webfetch-cli) is a client for a hosted fetch service. Report creation and sharing are covered by [Webstone's reporting documentation](https://github.com/lexmount/webstone/blob/main/docs/report-hosting.md).

## Citation

Cite Webstone and record the exact dataset revision used in your experiment. The canonical project citation is [CITATION.cff](https://github.com/lexmount/webstone/blob/main/CITATION.cff).

```bibtex
@software{webstone_2026,
  author = {{Webstone contributors}},
  title = {Webstone: Benchmarking Browsers and Web Tools for AI Agents},
  note = {Evaluation harness},
  year = {2026},
  url = {https://github.com/lexmount/webstone}
}
```

## Main and full content annotations

`content-scopes-530` adds paired `main` and `full` acceptance profiles for the
530 tasks of the v3.0.0 edition (`primary-content-20261103`). Both scopes are
available for every one of these tasks. It is a separate suite selection; the
edition's original requirements are unchanged. Existing tasks, viewer configurations and reference inputs are retained.

The cohort is scored by Webstone [`1287fdf8`](https://github.com/lexmount/webstone/commit/1287fdf859aaac56129933024abdf1246afbcfb7),
merged from [Webstone PR #268](https://github.com/lexmount/webstone/pull/268).
Export it with `lexbench-webfetch export --suite content-scopes-530 --source
<approved-input-directory> --output <new-directory>`. The approved input directory
must contain the frozen `dataset.json` bound by this suite's `source-inputs.json`.
Public reference hashes do not replace those inputs.

Use `webstone validate --dataset <exported-dataset.json>
--content-scope main` or `--content-scope full` to check a scope before a run.
A run without `--score-scope` scores both `main` and `full` from the same saved
responses, with no extra provider calls; `--score-scope main` or `--score-scope full`
computes only that scope. `--content-scope` selects the acquisition, retry and
report scope. The approved input bound by this cohort declares `main` as its default.
An explicit option overrides a saved scope; annotated tasks without a saved scope
fall back to `full`. Scoring both scopes in one run requires Webstone
[`ac6b3da4`](https://github.com/lexmount/webstone/commit/ac6b3da4beccedd800a45fa4d26ca0414e8cc79e)
([PR #276](https://github.com/lexmount/webstone/pull/276)) or later. Older suites keep their original rules. The existing manifest tooling pin still describes the
released edition; the cohort's `implementation` field identifies its scoring code.
