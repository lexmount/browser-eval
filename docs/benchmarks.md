# Benchmarks and the execution harness

Browser Eval executes tasks, adapts browsers and providers, calls the selected benchmark scorer, records evidence and builds reports. Benchmarks define which tasks to run and what counts as an acceptable result.

| Responsibility | Owner |
| --- | --- |
| Self-authored Web Fetch task populations, prompts and acceptance requirements | [LexBench Web Fetch](https://github.com/lexmount/lexbench-webfetch) |
| LexBench task catalogs and frozen source-input bindings | LexBench Web Fetch |
| Browser/provider adapters, execution policy and credential loading | Browser Eval |
| Task model, deterministic acceptance scorer, file-byte checks, compiler and scenario registry | LexBench Web Fetch |
| Rescoring orchestration and evidence revision machinery | Browser Eval |
| Experiment profiles, method versions, certificates and publication targets | Browser Eval |
| Report builders, evidence viewers, packaging and publication | Browser Eval |
| Full source captures, provider responses and measured runs | External input/evidence storage, outside both Git repositories |
| WebMainBench, WCXB and WebArena task definitions | Their upstream benchmark owners |

LexBench Web Fetch can be inspected, validated and exported without installing Browser Eval. Its public task requirements are data, so another runner can consume them. Browser Eval imports the pinned `lexbench_webfetch` task model and scoring implementation directly. LexBench can score saved responses and files without Browser Eval. Browser Eval owns the execution, evidence and report lifecycle around those calls.

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

Change task requirements in LexBench, freeze a new dataset/catalog/manifest version there, and select that version in a new experiment profile. Fix execution defects in Browser Eval and scoring defects in LexBench; keep accepted/rejected scenario controls with their owner. Preserve prior measurements and apply corrections across the declared affected scope.

The retained `harness/lexbench/` module concerns historical headless-browser comparisons. It is unrelated to the `lexbench-webfetch` input package and remains an execution/report adapter.

## Report instances

Each report track uses its maintained build, check and package entry points.
Browser Use and Browser Trajectory Replay accept an instance profile at each boundary. Keep the profile and measured evidence outside
Git. Select a new output directory; changing participants, sample size or report
content does not require another renderer or command script.

Browser Use profiles register models, browser labels and measurement controls.
`task_selection.path` and `task_selection.sha256` bind the selection file; its hash
must match `selection_sha256`. The selection declares its own `metadata.tier_counts`.
`official_dataset.path` and `official_dataset.sha256` can bind the exact historical
official task file, matching `official_data_checksum` and the recorded calls.
This allows rebuilding an older measurement after the installed benchmark has
changed. `failure_cause_review.source_path` may name an external, hash-bound review.
Keep original attempts and task contracts unchanged.

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
`scripts/build_report.py --profile evaluation/wcxb/current-report.json`. Run
`scripts/check_report.py` with that profile and `scripts/check_wcxb_visual.py`
against the output. The existing publication entry point also supports local
packaging without credentials or a network upload:

```sh
uv run --frozen python scripts/publish_wcxb_artifact_site.py --package-only \
  --source /absolute/instance/report --work /absolute/instance/package
```
