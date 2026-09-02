# Architecture

- `evaluation/dataset.json` owns current task rules and independently obtained references.
- Providers fetch content without scoring it. Credentials are expanded locally and never published.
- `metrics.py` and `dynamic_lists.py` evaluate persisted output. Task success depends on the task kind: body, resource, file or dynamic structure.
- `report_build.py` checks output hashes, stages selected evidence, then calls the sole renderer `report.py`. It replaces only a marked generated directory.
- `scripts/build_report.py` builds `reports/webfetch/` directly from `evaluation/`. There is no older-report dependency or compatibility renderer.

`evaluation/execution.json` associates the selected attempts with their actual runtime parameters and timestamps. Measurements retain original content hashes. `sources/` contains publishable reference evidence; signed-in raw HTML and private account information are excluded.

Formal pages cover rankings, datasets, methodology, results, provider descriptions and interface examples, environment, reproduction, integration, and account setup. Source coverage and access restrictions are factual limitations, not a user-assistance workflow.
