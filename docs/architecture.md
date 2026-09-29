# Architecture

- `evaluation/dataset.json` owns current task rules and independently obtained references.
- Providers fetch content without scoring it. Credentials are expanded locally and never published.
- `metrics.py` and `dynamic_lists.py` evaluate persisted output. Task success depends on the task kind: body, resource, file or dynamic structure.
- `report_build.py` checks output hashes, stages selected evidence, then calls the sole renderer `report.py`. It replaces only a marked generated directory.
- `scripts/build_report.py` builds `reports/webfetch/` directly from `evaluation/`. There is no older-report dependency or compatibility renderer.

The persistent evidence bundle's `execution.json` associates selected attempts with their actual runtime parameters and timestamps. Measurements retain original content hashes. Git's `evaluation/sources/` contains publishable reference evidence; signed-in raw HTML and private account information are excluded.

Formal pages cover rankings, datasets, methodology, results, provider descriptions and interface examples, environment, reproduction, and account setup. Source coverage and access restrictions are factual limitations, not a user-assistance workflow.

The report application owns full rules, interfaces and auditable records. Its tables retain local metric definitions and link to the matching methodology example. The interpretation article owns the argument, mechanism experiments, counterexamples and decision implications; it repeats only enough context to stand alone. The homepage provides quantitative overview and article access, not a second prose summary. Provider integration details belong with their interface examples, not a separate page repeating task boundaries.

## Browser trajectory replay ownership

- `harness/trajectory_replay/` owns reusable action execution, deployment rebinding, official evaluation, evidence validation and report contracts. Files in `scripts/` only expose those stages as commands.
- A replay dataset is browser-neutral. Participant profiles bind the browser or engine, binary, backend, deployment, accepted catalog, ledger and publication target. Adding Moli or another browser reuses the same runner and creates participant evidence outside Git.
- The repository keeps the current compact dataset and correction provenance. Physical attempts, intermediate catalogs, repair queues and report bundles remain in `BROWSER_EVAL_DATA_HOME`; `current-report.json` binds the accepted external artifacts by relative path and hash.
- Canonical task definitions, evaluator semantics and corrections that apply to every runner belong in `lexmount/webarena-verified` on protected branch `lex-main`. `browser-eval` pins an immutable commit from that branch; release checks reject the ServiceNow repository, PyPI implementation or an unapproved side branch.
- `browser-eval` owns browser participants, deployment binding, trajectory qualification and replay, BrowserTool actions, reset/readiness receipts, evidence collection, retry ledgers, replay-only postconditions, reports and publication. Environment-specific references stay here with provenance instead of changing the canonical benchmark.
- A temporary evaluator compatibility patch may remain in `browser-eval` only while a proven generic fix is pending in the fork. It needs paired behavioral controls and must be removed after the pinned `lex-main` commit contains the fix.
