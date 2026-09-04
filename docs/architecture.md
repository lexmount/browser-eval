# Architecture

- `evaluation/dataset.json` owns current task rules and independently obtained references.
- Providers fetch content without scoring it. Credentials are expanded locally and never published.
- `metrics.py` and `dynamic_lists.py` evaluate persisted output. Task success depends on the task kind: body, resource, file or dynamic structure.
- `report_build.py` checks output hashes, stages selected evidence, then calls the sole renderer `report.py`. It replaces only a marked generated directory.
- `scripts/build_report.py` builds `reports/webfetch/` directly from `evaluation/`. There is no older-report dependency or compatibility renderer.

The persistent evidence bundle's `execution.json` associates selected attempts with their actual runtime parameters and timestamps. Measurements retain original content hashes. Git's `evaluation/sources/` contains publishable reference evidence; signed-in raw HTML and private account information are excluded.

Formal pages cover rankings, datasets, methodology, results, provider descriptions and interface examples, environment, reproduction, and account setup. Source coverage and access restrictions are factual limitations, not a user-assistance workflow.

The report application owns full rules, interfaces and auditable records. Its tables retain local metric definitions and link to the matching methodology example. The interpretation article owns the argument, mechanism experiments, counterexamples and decision implications; it repeats only enough context to stand alone. The homepage provides quantitative overview and article access, not a second prose summary. Provider integration details belong with their interface examples, not a separate page repeating task boundaries.
