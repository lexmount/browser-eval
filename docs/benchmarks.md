# Benchmarks and the execution harness

Browser Eval owns scoring, execution adapters, saved evidence and reports. Public task definitions, source attribution and reference bindings are versioned on Hugging Face.

| Responsibility | Owner |
| --- | --- |
| Web Fetch task model, deterministic acceptance and offline scoring | `src/lexbench_webfetch/` in this repository |
| Search acceptance, relevance metrics and offline scoring | `src/lexbench_websearch/` in this repository |
| Public task definitions and source/reference bindings | The [LexMount benchmark datasets on Hugging Face](https://huggingface.co/Lexmount) |
| Download verification and dataset selection | `src/harness/benchmarks/` and its [data lock](../src/harness/benchmarks/data-lock.json) |
| Provider calls, browser execution, evidence and reports | Existing track modules under `src/harness/` |
| Qualified WCXB inputs and curated/public replay definitions | Their HF editions; upstream projects retain source attribution |
| WebMainBench and WebArena evaluator code | The selected scorer/evaluator dependency and its recorded source revision |

The two scorer APIs and console commands ship in the Browser Eval distribution. They require no browser installation or provider credentials. Former standalone scorer repositories retain source history and attribution; current installation and scoring use Browser Eval.

## Evaluation design

The README introduces the [design principles](../README.md#evaluation-approach) and [workflow diagrams](../README.md#evaluation-tracks). Benchmark integrations also need the following scoring and review evidence.

### Scoring evidence

Retain the scorer identity and its input evidence separately from computed verdicts. For Search relevance, use named human annotations by default. If a model judge is explicitly selected, preserve its model, prompt, rubric, candidate evidence, raw responses and grades; label model-judged results accordingly. See the [Search scoring guide](../src/harness/web_search/README.md) and [Web Fetch scoring corrections](../src/harness/web_fetch/README.md#correct-a-score-or-reference) for their executable contracts.

### Independent verification

Independent verification is opt-in. Declare its scope and retain the reviewer, protocol and hashes of the evidence reviewed. The default scope is machine failures; reviewing passes or the full matrix requires an explicit request. Reports state reviewed/total coverage and accepted unreviewed passes.

When verification finds a defect, correct the shared cause in the reference, scorer or execution stage. Retain original evidence and official scores; record corrections separately and rescore or rerun the affected scope under the revised contract. A scorer-only correction can reuse valid saved outputs. Use the existing [Web Fetch review workflow](../src/harness/web_fetch/README.md#request-an-independent-review) and [replay protocol](trajectory-replay-protocol.md) for track-specific handling.

## Use benchmark inputs

Current datasets are published separately:

| Dataset | Purpose |
| --- | --- |
| [LexBench Web Fetch](https://huggingface.co/datasets/Lexmount/lexbench-webfetch) | URL-to-content tasks, acceptance contracts and reference bindings |
| [LexBench Web Search](https://huggingface.co/datasets/Lexmount/lexbench-websearch) | Scenario queries and source qualifications |
| [LexBench SimpleQA Verified](https://huggingface.co/datasets/Lexmount/lexbench-simpleqa-verified) | A separate retrieval adaptation of the publisher's factual questions |
| [LexBench WebMainBench](https://huggingface.co/datasets/Lexmount/lexbench-webmainbench) | Corrected frozen-HTML extraction inputs and references |
| [LexBench WCXB](https://huggingface.co/datasets/Lexmount/lexbench-wcxb) | Source-qualified extraction inputs, preserving selected upstream HTML and reference bytes |
| [LexBench Trajectory Replay](https://huggingface.co/datasets/Lexmount/lexbench-trajectory-replay) | Curated fixed-action tasks and trajectories |
| [LexBench WebArena Replay](https://huggingface.co/datasets/Lexmount/lexbench-webarena-replay) | Separate human-recording, corrected-task and full official-task configurations |

Dataset cards explain scope, schema, source terms and loading. Configuration counts and selected revisions come from the dataset registry and data lock. WebArena's full task configuration retains both portable and direct-only tasks; inclusion in that configuration does not establish portable replay qualification. The official evaluator remains a separately pinned dependency.


Install Browser Eval, then prepare its pinned public definitions:

```sh
uv sync --frozen
uv run --frozen python -m harness.benchmarks.data
uv run --frozen lexbench-webfetch list
uv run --frozen lexbench-webfetch validate
uv run --frozen lexbench-websearch list
```

When upgrading an existing checkout, `uv sync --frozen` removes the retired standalone scorer distributions before installing the shared distribution.

Downloads use full Hugging Face commits and verify the archive and every file. Public downloads need no access key. Cached definitions work with `HF_HUB_OFFLINE=1`; `BROWSER_EVAL_DATASET_CACHE` overrides the default `~/.browser-eval/datasets/`. Changing the evidence directory does not change the selected dataset cache.

If validation reports extra, missing or corrupted cached files, run `uv run --frozen python -m harness.benchmarks.data --repair <track>` while online. Repair preserves the invalid directory beside the snapshot, then downloads and verifies the locked edition again. Offline repair leaves the existing files intact.

The public Fetch definitions contain compact task contracts and reference hashes. Full third-party captures still require an approved matching input copy:

```sh
uv run --frozen lexbench-webfetch export \
  --suite webfetch --source /absolute/path/approved-inputs \
  --output /absolute/path/new-input-directory
uv run --frozen browser-harness-eval validate \
  --dataset /absolute/path/new-input-directory/dataset.json
```

The export verifies the full dataset and declared reference files before materializing them. A formal live-site comparison uses the [certified runner](../src/harness/web_fetch/README.md) and the selected track's release gates. Ordinary CLI runs are diagnostic. For offline scoring of an existing answer, use `lexbench-webfetch score` or `lexbench-websearch score`; their help lists the task, response and evidence inputs.

## Historical bindings and new revisions

[The data lock](../src/harness/benchmarks/data-lock.json) selects dataset commits, records archive/file hashes and retains earlier snapshots. [Fetch](../evaluation/lexbench-webfetch.json) and [Search](../evaluation/lexbench-websearch.json) import bindings preserve logical historical paths and exact definition hashes. Existing experiment profiles and certificates retain their original identities.

Select a new published edition through one command:

```sh
make pin-dataset DATASET=webfetch REV=<full-Hugging-Face-commit>
```

The same command selects the other dataset keys registered in the lock, including `websearch`, `simpleqa-verified`, `webmainbench`, `wcxb`, `trajectory-replay` and `webarena-replay`. The command verifies the release before updating the lock and applicable import bindings. Keep current-version selections in these files, not in README prose.

Author dataset changes in a checkout of the corresponding Hugging Face dataset. Commit the definitions and materialize any Git LFS files, then use `uv run --frozen python -m harness.benchmarks.publication --help` to build and publish the inspected commit. Install the optional `dataset-publish` extra and authenticate with `HF_TOKEN` for uploads; readers need no token. Preserve upstream attribution and redistribution terms. Scores, provider responses, model grades and private run directories do not belong in a dataset release.

For resource-only or raw-file tasks, declare `content_score_applicable: false` when no text answer is required; file/resource checks remain active. Fix scoring rules here and keep positive/negative controls with their scorer. Preserve prior measurements; a scoring or reference correction applies to the declared affected scope through the existing revision receipts. `src/harness/lexbench/` remains the historical headless-browser execution/report adapter, separate from the two scorer packages.

The [curated replay dataset](https://huggingface.co/datasets/Lexmount/lexbench-trajectory-replay) records its adaptation from [Lexbench-Headless-Browser scenario designs](https://github.com/lexmount/Lexbench-Headless-Browser/tree/3f1580a20922fc3f339b5fc279d9bbdb06d5d194/tasks/scenarios). The separate [WebArena replay dataset](https://huggingface.co/datasets/Lexmount/lexbench-webarena-replay) supplies task and recording editions for the [public replay runtime](../examples/trajectory_replay/public_webarena/README.md). Run both through Browser Eval; the upstream repositories provide attribution and the pinned official evaluator, not an alternative current installation path.

## Benchmark editions

Every track identifies its benchmark code by the full source commit. A release
tag may provide a readable label, but the selected tag must resolve to a recorded
commit. Freeze task, reference and scoring-file hashes as well; a package version
or a moving branch name alone does not identify the evaluation.

Use the existing owner for each selection:

| Benchmark | Version authority |
| --- | --- |
| LexBench datasets | `src/harness/benchmarks/data-lock.json`, corresponding import bindings and the Browser Eval scorer source commit |
| WebMainBench | The data lock's corrected corpus, its upstream/correction provenance and the selected run profile's scorer commit |
| WCXB | Qualified HF edition in the data lock, source manifest and unchanged HTML/reference hashes |
| WebArena replay data | HF edition in the data lock and selected task/trajectory bindings |
| WebArena evaluator for Browser Use and replay | Pinned evaluator dependency and runtime source verification |
| Curated replay | HF edition in the data lock, Browser Eval source commit and selected task/trajectory hashes |

Read and validate the selected binding before execution; retain it with the run
and report. For data distributed as files, preserve content hashes even when no
upstream commit is available. Do not invent a source commit for an old snapshot.

Current-use documentation links to these owners instead of repeating selected
versions or commits. Upstream `main` links in guides are navigation, not execution
inputs. Historical experiments and source attributions keep their immutable
citations; those records must not follow a newer dependency selection.

Updating the installed benchmark does not select a new dataset for an existing
report.

Hugging Face release tags are optional labels for immutable dataset commits; a moving tag never replaces the recorded commit.
Browser Eval publishes software versions as `vMAJOR.MINOR.PATCH` and records the
full Git commit for execution and report-building code.
Reports reference their benchmark edition. A published build also records the hosting platform's immutable version ID.

Report home pages link to the Browser Eval build version and the selected bench
edition. Their JSON bindings retain code commits and source/input hashes.
Rebuilding with a newer Browser Eval release changes the build identity, not the
saved call identity or benchmark edition. Custom datasets without a frozen
edition retain their hashes rather than inheriting the installed catalog version.

| Change | Edition | Result comparison |
| --- | --- | --- |
| Documentation or presentation only | Patch | Direct when execution controls also match |
| Reviewed scoring/reference correction with the same task inputs | Minor | Re-score both saved cohorts under one edition |
| Added/removed cases, changed requests, output scope or routes | Major | Separate full-population results |

The `lexbench_webfetch` module owns `lexbench-webfetch release` and `compare-releases`. Historical benchmark version contracts remain available with their original source revisions.
Freeze `benchmark-release.json` beside the compiled inputs before calls.
The certified Web Fetch runner accepts `--benchmark-release`; it also discovers
the sidecar beside the selected dataset. Release creation records the full scorer
commit alongside the independently computed source hash. The runner checks tasks and the active scorer
before external work and retains the binding in execution metadata. The original
call edition remains in benchmark-input-release.json; a report may select a newer
scoring edition after rescoring, retaining both files.

An instance profile can select an edition explicitly:

```json
{
  "benchmark_release": {
    "path": "benchmark-release.json",
    "sha256": "<SHA-256 of the selected sidecar>"
  }
}
```

Paths are relative to the external profile. The existing build copies this
edition into the report manifest and package and displays a link beside the
home-page scope. Checks reject mismatched populations, acceptance rules,
scoring sources, missing sidecars and incorrect version labels. Historical
hash-bound reports remain usable without inventing a version label. An
explicit historical edition preserves the saved scorer identity during a
presentation rebuild; it does not run the current scorer over old results.

Reuse unchanged saved outputs for a minor correction, including prior
successes, then recompute scores under one edition. Use the existing revision
receipts; do not overwrite the older report. Major editions may retain
diagnostic task-overlap analyses, but their complete throughputs/pass rates
are separate measurements. Latency still requires matching runtime controls
and the existing same-window comparison contract.

## Report instances

Local reports are optional outputs from saved evidence. The public CLI supports `run --no-report` and `rescore --no-report`; `report <run-directory>` can build HTML later without new calls. Local use requires no report-hosting account. Formal release checks and packaging apply when preparing a release; upload requires an explicit publication request and authorization. See [local reports and optional sharing](report-hosting.md).

Each report track uses its maintained build, check and package entry points.
Browser Use and Browser Trajectory Replay accept an instance profile at each boundary. Keep the profile and measured evidence outside
Git. Select a new output directory; changing participants, sample size or report
content does not require another renderer or command script.

The native Web Fetch report also accepts an external profile. Bind the frozen
dataset, complete participant matrix, execution settings and evidence hashes in
that profile. Put report prose in the evidence tree and bind it through
`comparison.report_interpretation`; a report refresh does not require changing
Python strings. The normal build and check retain certification and review gates.
The diagnostic build is a separate evidence state and does not certify results.

```sh
uv run --frozen python scripts/build_report.py \
  --profile /absolute/instance/profile.json \
  --evidence /absolute/instance/evidence --output /absolute/instance/report
uv run --frozen python scripts/check_report.py \
  /absolute/instance/report --profile /absolute/instance/profile.json
```

A profile stored outside Git has the same validation rules as a repository profile.
Repository defaults select the maintained published report; they are optional
convenience paths, not a requirement to commit new report instances.

Browser Use profiles register models, browser labels and measurement controls.
`task_selection.path` and `task_selection.sha256` bind the selection file; its hash
must match `selection_sha256`. The selection declares its own `metadata.tier_counts`.
`official_dataset.path` and `official_dataset.sha256` can bind the exact historical
official task file, matching `official_data_checksum` and the recorded calls.
This allows rebuilding an older measurement after the installed benchmark has
changed. `failure_cause_review.source_path` may name an external, hash-bound review.
Keep original attempts and task contracts unchanged.

An optional `report_history` object binds a JSON file through `path` and `sha256`.
Relative paths use the authority root; absolute paths can select external content.
A new instance without this binding has no release history. It never inherits the
maintained report's releases or review rounds. Build, check and package use the
same binding.

```sh
uv run --frozen python scripts/build_browser_use_report.py \
  --profile /absolute/instance/profile.json \
  --evidence /absolute/model-evidence --output /absolute/instance/report
uv run --frozen python scripts/check_browser_use_report.py \
  /absolute/instance/report --profile /absolute/instance/profile.json \
  --evidence /absolute/model-evidence --require-complete
uv run --frozen python scripts/package_browser_use_artifact_hub.py \
  --report /absolute/instance/report --profile /absolute/instance/profile.json \
  --evidence /absolute/model-evidence --output /absolute/instance/report.zip \
  --attestation /absolute/instance/attestation.json \
  --visual-receipt /absolute/instance/visual.json
```

Repeat `--evidence` for additional model cohorts. The renderer derives comparison
counts and failure examples from the selected results. Missing task-type or
execution metadata stays unavailable; it is never inferred from historical IDs.

Trajectory Replay profiles bind the dataset, participant registry, execution
protocol and reference validity. The failure review must bind the exact saved
results. Without an authored content binding, the maintained renderer generates
an interpretation from the current results, including complete failure
distributions and specimen links. An optional authored interpretation uses
`--content`, `--content-bindings` and `--interpretation-audit`; these inputs must
belong to the selected evidence.

```sh
uv run --frozen python scripts/build_webarena_replay_report.py \
  --profile /absolute/instance/profile.json --results /absolute/results.json \
  --review /absolute/failure-review.json --output /absolute/instance/report
uv run --frozen python scripts/check_webarena_replay_report.py \
  /absolute/instance/report --profile /absolute/instance/profile.json
uv run --frozen python scripts/package_webarena_replay_artifact_site.py \
  --report /absolute/instance/report --profile /absolute/instance/profile.json \
  --evidence /absolute/results.json --review /absolute/failure-review.json \
  --output /absolute/instance/report.zip \
  --attestation /absolute/instance/attestation.json \
  --visual-receipt /absolute/instance/visual.json
```

Both packers run integrity, link and browser checks before installing an archive.
The replay packer automatically uses the existing compact representation above
its file-count limit. It verifies every task/result page and evidence payload
against the uncompressed report, then exercises exported routes in every saved
language. Compression preserves all logical results and physical-call evidence.

The Make targets accept `PROFILE`, `OUTPUT` and `VISUAL_RECEIPT` for these tracks;
replay build/package targets also accept `REVIEW`, and packaging accepts `ARCHIVE`
and `ATTESTATION`. Packaging creates a local archive and does not publish it.

For WCXB, select a profile-bound evidence directory and a fresh output with
`scripts/build_report.py --profile /absolute/instance/profile.json`. Keep its
`report-content-binding.json` beside the profile. `providers` declares the matrix;
`participants` can supply labels, bilingual routes, interface examples and account
requirements. An optional `paired_comparison` names `baseline` and `candidate`
participants. A declared timing correction still requires complete paired evidence;
ordinary cohorts do not inherit another report’s timing or optional-feature claims. Run
`scripts/check_report.py` with that profile and `scripts/check_wcxb_visual.py`
against the output. The existing publication entry point also supports local
packaging without credentials or a network upload:

```sh
uv run --frozen python scripts/publish_wcxb_artifact_site.py --package-only \
  --profile /absolute/instance/profile.json \
  --source /absolute/instance/report --work /absolute/instance/package
```

### Re-score a published snapshot

A `published-snapshot-audit` profile rebuilds a saved Web Fetch report through the
current renderer. Bind its complete exported file manifest, original and corrected
results, historical verdicts, initial recomputation, scoring receipt, review,
interpretation and diagram under `inputs`. An optional bound `method_registry`
provides historical participant IDs, engines and labels; `release_history` supplies
the report's structured history. Keep these instance files outside Git.

`score_published_snapshot` verifies the full task-by-method matrix, preserves call
identities and timing, and computes scores without contacting providers. It accepts
an exact hash-verified recovered original, otherwise identifies a privacy-projected
input separately. A projected file alone is not evidence that redaction changed a
verdict. Compare the old scorer on the same bytes before assigning that cause.

Build with `scripts/build_report.py --profile <profile> --evidence <snapshot>
--output <new-report> --language zh`. This historical format supports Chinese and
provides an English not-generated notice. Check with `scripts/check_report.py
<new-report> --profile <profile> --visual-receipt <private-receipt>`, then package
through `scripts/package_artifact_hub.py <new-report> <package.zip> --snapshot-audit`.
Run these Python entries through `uv run --frozen python`. The mode retains the
original routes and metric populations and labels the new report as a saved-input
audit; it does not certify new calls or replace the original release. The ordinary
packaging path rejects this evidence state without the explicit audit flag.

When publication is requested and authorized, publish through `scripts/publish_artifact_hub.py --snapshot-audit` with a separate
publication target, receipt and operation journal. Use `--create` for a new report
and set visibility explicitly. A later update uses that report's own site and
expected version. Keep the original report's target unchanged.

### Participant interface metadata

A report can bind participant documentation through
`comparison.method_registry`. Each participant selects its execution `engine`
and label. Optional `report_access` contains `account`, `configuration`,
`env_vars` and `links`; optional `report_interface` contains `entry`, `auth`,
`params`, `fields`, `request`, `source` and `notes`. These objects follow the
shared interface renderer's existing format. Use them for cohort-specific
instructions and examples. Measured version fields and binary hashes remain
part of each saved call; a participant name is not a version receipt.

Shared adapter documentation describes interfaces by engine. Adding a version
or renaming a participant requires data changes, without another renderer entry.

## WCXB inputs

The runtime defaults to the qualified [HF edition](https://huggingface.co/datasets/Lexmount/lexbench-wcxb) selected by the data lock. Its source manifest records upstream identities and qualification exclusions; selected HTML and reference bytes are retained unchanged. Historical profiles keep their original manifests and input hashes. The [upstream WCXB project](https://github.com/Murrough-Foley/web-content-extraction-benchmark) remains the source attribution. Use Browser Eval's existing runner and scorer for current execution.

## WebMainBench runtime

The WebMainBench runner defaults to the corrected corpus selected by the data lock. Its scoring command prepares the exact declared scorer checkout when `--scorer-repo` is omitted, and rejects a changed commit or dirty tracked files. Install the `webmainbench-eval` extra for its metric dependencies. Explicit dataset files retain the original-corpus hash and scorer defaults; provide the matching hash and scorer commit when selecting another historical edition. Existing profiles and saved evidence remain unchanged.

## Web Search integration

The local `lexbench_websearch` module loads its pinned public definitions through the shared dataset loader. The registry's `current` mapping selects named task suites; explicit dataset, field and hash references are resolved before collection. Historical standalone profiles preserve their original bytes and hashes.

Search providers, annotation collection, latency/billing evidence, audits and reports remain in `src/harness/web_search/`. Report profiles and measured results stay beside external evidence. Change task data on Hugging Face and scorer code here; keep the existing source qualifications and paired scoring controls. The [Search dataset](https://huggingface.co/datasets/Lexmount/lexbench-websearch) and [SimpleQA retrieval edition](https://huggingface.co/datasets/Lexmount/lexbench-simpleqa-verified) retain source attribution and derivation details.
