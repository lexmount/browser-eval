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

This uses the existing dataset interface. `uv sync --frozen` installs LexBench from the exact Git commit pinned in `pyproject.toml` and `uv.lock`. The pinned commit is merged into LexBench’s main branch. uv retrieves the Git source, builds a wheel and installs the Python package; Git must be available, but no sibling checkout is required. The input checker verifies frozen bytes; a formal live-site comparison still requires the [certified runner](../harness/web_fetch/README.md), independent reference validation before and after calls, and the selected track's release gates. Ordinary CLI runs are diagnostic.

## Historical bindings and new revisions

The four initial LexBench suites preserve the original catalogs and source-input manifests. [The import binding](../evaluation/lexbench-webfetch.json) records the benchmark revision and exact hashes. The original catalogs, manifests and task-authoring contracts live only in LexBench, with their frozen bytes and relative paths preserved. Browser Eval resolves historical logical input paths through the installed package. Its experiment profiles and certificates retain their original bindings.

Change task requirements in LexBench, freeze a new dataset/catalog/manifest version there, and select that version in a new experiment profile. Fix execution defects in Browser Eval and scoring defects in LexBench; keep accepted/rejected scenario controls with their owner. Preserve prior measurements and apply corrections across the declared affected scope.

The retained `harness/lexbench/` module concerns historical headless-browser comparisons. It is unrelated to the `lexbench-webfetch` input package and remains an execution/report adapter.
