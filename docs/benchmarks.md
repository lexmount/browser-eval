# Benchmarks and the execution harness

Browser Eval executes tasks, adapts browsers and providers, applies generic scoring rules, records evidence and builds reports. Benchmarks define which tasks to run and what counts as an acceptable result.

| Responsibility | Owner |
| --- | --- |
| Self-authored Web Fetch task populations, prompts and acceptance requirements | [LexBench Web Fetch](https://github.com/lexmount/lexbench-webfetch) |
| LexBench task catalogs and frozen source-input bindings | LexBench Web Fetch |
| Browser/provider adapters, execution policy and credential loading | Browser Eval |
| Generic scoring implementation, scenario regressions and revision machinery | Browser Eval |
| Experiment profiles, method versions, certificates and publication targets | Browser Eval |
| Report builders, evidence viewers, packaging and publication | Browser Eval |
| Full source captures, provider responses and measured runs | External input/evidence storage, outside both Git repositories |
| WebMainBench, WCXB and WebArena task definitions | Their upstream benchmark owners |

LexBench Web Fetch can be inspected, validated and exported without installing Browser Eval. Its public task requirements are data, so another runner can consume them. Browser Eval remains one implementation of their execution and generic scoring. An alternate scorer must identify its version and demonstrate equivalent acceptance behavior before its scores can be compared.

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

This uses the existing dataset interface. No sibling checkout, installed LexBench Python package or copied task generator is required by Browser Eval. The input checker verifies frozen bytes; a formal live-site comparison still requires the [certified runner](../harness/web_fetch/README.md), independent reference validation before and after calls, and the selected track's release gates. Ordinary CLI runs are diagnostic.

## Historical bindings and new revisions

The four initial LexBench suites preserve the original catalogs and source-input manifests. [The import binding](../evaluation/lexbench-webfetch.json) records the benchmark revision and exact hashes. Browser Eval retains the original `evaluation/` records because existing experiment profiles, certificates and source-import commands refer to their bytes. Those historical records must not be edited to author new LexBench task versions.

Change task requirements in LexBench, freeze a new dataset/catalog/manifest version there, and select that version in a new experiment profile. Fix generic execution or scoring defects in Browser Eval and keep accepted/rejected scenario controls. Preserve prior measurements and apply corrections across the declared affected scope.

The retained `harness/lexbench/` module concerns historical headless-browser comparisons. It is unrelated to the `lexbench-webfetch` input package and remains an execution/report adapter.
