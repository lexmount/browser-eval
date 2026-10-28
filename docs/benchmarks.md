# Benchmarks and the execution harness

Browser Eval executes tasks, adapts browsers and providers, calls the selected benchmark scorer, records evidence and builds reports. Benchmarks define which tasks to run and what counts as an acceptable result.

| Responsibility | Owner |
| --- | --- |
| Self-authored Web Fetch task populations, prompts and acceptance requirements | [LexBench Web Fetch](https://github.com/lexmount/lexbench-webfetch) |
| LexBench task catalogs and frozen source-input bindings | LexBench Web Fetch |
| Web Search tasks, provenance, source qualifications and deterministic retrieval scoring | [LexBench Web Search](https://github.com/lexmount/lexbench-websearch) |
| Browser/provider adapters, execution policy and credential loading | Browser Eval |
| Task model, deterministic acceptance scorer, file-byte checks, compiler and scenario registry | LexBench Web Fetch |
| Rescoring orchestration and evidence revision machinery | Browser Eval |
| Experiment profiles, method versions, certificates and publication targets | Browser Eval |
| Report builders, evidence viewers, packaging and publication | Browser Eval |
| Full source captures, provider responses and measured runs | External input/evidence storage, outside both Git repositories |
| WebMainBench, WCXB and WebArena task definitions | Their upstream benchmark owners |

LexBench Web Fetch can be inspected, validated and exported without installing Browser Eval. Its public task requirements are data, so another runner can consume them. Browser Eval imports the pinned `lexbench_webfetch` task model and scoring implementation directly. LexBench can score saved responses and files without Browser Eval. Browser Eval owns the execution, evidence and report lifecycle around those calls.

## Evaluation design

The README introduces the [design principles](../README.md#evaluation-approach) and [workflow diagrams](../README.md#evaluation-tracks). Benchmark integrations also need the following scoring and review evidence.

### Scoring evidence

Retain the scorer identity and its input evidence separately from computed verdicts. For Search relevance, use named human annotations by default. If a model judge is explicitly selected, preserve its model, prompt, rubric, candidate evidence, raw responses and grades; label model-judged results accordingly. See the [Search scoring guide](../harness/web_search/README.md) and [Web Fetch scoring corrections](../harness/web_fetch/README.md#correct-a-score-or-reference) for their executable contracts.

### Independent verification

Independent verification is opt-in. Declare its scope and retain the reviewer, protocol and hashes of the evidence reviewed. The default scope is machine failures; reviewing passes or the full matrix requires an explicit request. Reports state reviewed/total coverage and accepted unreviewed passes.

When verification finds a defect, correct the shared cause in the reference, scorer or execution stage. Retain original evidence and official scores; record corrections separately and rescore or rerun the affected scope under the revised contract. A scorer-only correction can reuse valid saved outputs. Use the existing [Web Fetch review workflow](../harness/web_fetch/README.md#request-an-independent-review) and [replay protocol](trajectory-replay-protocol.md) for track-specific handling.

## Use LexBench inputs

In a LexBench Web Fetch checkout:

```sh
uv sync --frozen
uv run --frozen lexbench-webfetch list
uv run --frozen lexbench-webfetch validate
uv run --frozen lexbench-webfetch export \
  --suite webfetch --source /absolute/path/approved-inputs \
  --output /absolute/path/new-input-directory
```

The public definitions are included. Full third-party references require an approved matching input copy; the benchmark currently provides no public full-input download bundle. The export checks the original dataset and every declared reference file by hash, then materializes only those inputs. Select `popular-sites`, `dynamic-content` or `access-restrictions` for another suite.

In Browser Eval:

```sh
uv run --frozen browser-harness-eval validate \
  --dataset /absolute/path/new-input-directory/dataset.json
uv run --frozen browser-harness-eval run \
  --dataset /absolute/path/new-input-directory/dataset.json \
  --provider-config configs/providers.quickstart.json --providers http
```

This uses the existing dataset interface. `uv sync --frozen` installs LexBench from the exact Git commit pinned in `pyproject.toml` and `uv.lock`. uv retrieves the Git source, builds a wheel and installs the Python package; Git must be available, but no sibling checkout is required. The input checker verifies frozen bytes; a formal live-site comparison still requires the [certified runner](../harness/web_fetch/README.md), independent reference validation before and after calls, and the selected track's release gates. Ordinary CLI runs are diagnostic.

## Historical bindings and new revisions

The four initial LexBench suites preserve the original catalogs and source-input manifests. [The import binding](../evaluation/lexbench-webfetch.json) records the benchmark revision and exact hashes. The original catalogs, manifests and task-authoring contracts live only in LexBench, with their frozen bytes and relative paths preserved. Browser Eval resolves historical logical input paths through the installed package. Its experiment profiles and certificates retain their original bindings.

Change task requirements in LexBench, release the updated benchmark there, and select that version in a new experiment profile. Fix execution defects in Browser Eval and scoring defects in LexBench; keep accepted/rejected scenario controls with their owner. Preserve prior measurements and apply corrections across the declared affected scope.

The retained `harness/lexbench/` module concerns historical headless-browser comparisons. It is unrelated to the `lexbench-webfetch` input package and remains an execution/report adapter.

The curated [Browser Trajectory Replay suite](../examples/trajectory_replay/README.md) records [Lexbench-Headless-Browser scenario designs](https://github.com/lexmount/Lexbench-Headless-Browser/tree/3f1580a20922fc3f339b5fc279d9bbdb06d5d194/tasks/scenarios) in each applicable task's `source.design_basis`. It adapts those capabilities to public-page actions with its own exact expected outputs. This suite does not execute the complete upstream task population. The [public WebArena replay](../examples/trajectory_replay/public_webarena/README.md) instead uses qualified human recordings and the WebArena-Verified evaluator.

## Benchmark editions

Every track identifies its benchmark code by the full source commit. A release
tag may provide a readable label, but the selected tag must resolve to a recorded
commit. Freeze task, reference and scoring-file hashes as well; a package version
or a moving branch name alone does not identify the evaluation.

Use the existing owner for each selection:

| Benchmark | Version authority |
| --- | --- |
| LexBench Web Fetch / Web Search | Git dependencies in `pyproject.toml`, resolved commits in `uv.lock`, and `evaluation/lexbench-*.json` |
| WebMainBench | Selected run profile's scorer commit and frozen dataset/scorer hashes |
| WCXB | Selected source manifest's `source_commit` and input hashes |
| WebArena for Browser Use and replay | Pinned evaluator dependency, runtime source verification and the selected task/trajectory bindings |
| Repository-curated replay | Browser Eval source commit and selected task/trajectory hashes |

Read and validate the selected binding before execution; retain it with the run
and report. For data distributed as files, preserve content hashes even when no
upstream commit is available. Do not invent a source commit for an old snapshot.

Current-use documentation links to these owners instead of repeating selected
versions or commits. Upstream `main` links in guides are navigation, not execution
inputs. Historical experiments and source attributions keep their immutable
citations; those records must not follow a newer dependency selection.

Updating the installed benchmark does not select a new dataset for an existing
report.

Create `bench-vMAJOR.MINOR.PATCH` tags only in the benchmark repository.
Browser Eval publishes software versions as `vMAJOR.MINOR.PATCH` and records the
full Git commit for execution and report-building code.
Reports reference their benchmark edition and the hosting platform's immutable
version ID for each published build.

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

The task package owns `lexbench-webfetch release` and `compare-releases`.
See its [version contract](https://github.com/lexmount/lexbench-webfetch/blob/bench-v2.0.1/docs/versions.md).
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

Publish through `scripts/publish_artifact_hub.py --snapshot-audit` with a separate
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

## Web Search integration

LexBench Web Search is installed from the commit pinned in [pyproject.toml](../pyproject.toml), [uv.lock](../uv.lock) and the [definition binding](../evaluation/lexbench-websearch.json). Its task files and scoring code have one owner in that repository. Browser Eval provides providers, model/human annotation collection, latency/billing collection, audits and report publication. Report-specific execution profiles and receipts live with external evidence, selected explicitly by the caller. No sibling checkout is required.

The pinned benchmark's `version` command checks its catalog and scoring state. Select task suites through the installed registry's `current` mapping; the bench edition fixes their snapshots. The harness resolves explicit dataset/field/hash references before freezing a run. Resolving the split historical profiles reproduces the original profile bytes and hashes, so saved calls keep their original identity. Model judgment evidence and measured results stay outside both repositories.

Change search queries, references or scoring in LexBench Web Search and pin the resulting commit here. Change provider behavior or report generation in Browser Eval. See the upstream [data provenance](https://github.com/lexmount/lexbench-websearch/blob/main/docs/data.md) and [evaluation protocol](https://github.com/lexmount/lexbench-websearch/blob/main/docs/evaluation.md).

Update a merged Search benchmark revision with `make bump-lexbench BENCHMARK=lexbench-websearch REV=<full-commit>`. The shared updater checks upstream main ancestry and updates the package, lock and dataset-hash binding together.
