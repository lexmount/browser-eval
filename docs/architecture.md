# Architecture

- Each track’s current profile selects its dataset, participants, evidence bindings and publication target. `evaluation/dataset.json` is the historical live-URL Web Fetch task set.
- Providers fetch content without scoring it. Credentials are expanded locally and never published.
- `metrics.py` and `dynamic_lists.py` evaluate persisted output. Task success depends on the task kind: body, resource, file or dynamic structure.
- `report_build.py` checks output hashes, stages selected evidence, then calls the sole renderer `report.py`. It replaces only a marked generated directory.
- `scripts/build_report.py` builds `reports/webfetch/` from an explicit persistent evidence directory. There is no older-report dependency or compatibility renderer.

The persistent evidence bundle's `execution.json` associates selected attempts with their actual runtime parameters and timestamps. Measurements retain original content hashes. Reference evidence retains its source and licensing scope; source capture alone does not authorize redistribution. Signed-in raw HTML and private account information are excluded.

WebMainBench cohort certification is owned by `harness/web_fetch/webmainbench_certification.py`; scoring and score verification by `webmainbench_scoring.py`; full-cohort aggregation by `webmainbench_assembly.py`; and shared result-matrix, variant-identity and physical-call checks by `webmainbench_evidence.py`. Their command files delegate to these modules. Evidence-copy boundaries reject file and directory symlinks as well as escaping paths.

Complete WebMainBench comparisons assemble the baseline and every registry-declared variant directly from their certified cohorts. Participant counts come from profiles and the registry throughout assembly, rendering and release checks. Only declared evidence enters the package; assembly commits a new directory after all checks pass. Replacing a cohort creates a new package and retains the previous one. See [the WebMainBench guide](../harness/web_fetch/WEBMAINBENCH.md) for the supported commands and historical binding format.

Independent HTTP source capture and HTML extraction belong to `harness/web_fetch/source_capture.py`; its command is `scripts/collect_source_evidence.py`. Reference audits, scope refreshes and browser observations import the same extractor. `document_text.py` owns document extraction for both captured sources and provider responses, with explicit whitespace preservation for source reconstruction. Capture writes a new external evidence directory, validates the complete requested task scope before requests, and retains failed HTTP observations for review. Capture alone neither approves a reference nor certifies a provider result.

`harness/web_fetch/reference_inputs.py` resolves dataset-bound source inputs. `evaluation/source-inputs.json` binds the historical dataset and every external input by SHA-256; readers verify the installed inventory before using it. Missing or changed inputs cannot fall back to a checkout copy. Custom datasets without a manifest use their own adjacent inputs. Import and reconstruction commands are documented in [reference inputs](reference-inputs.md).

`harness/web_fetch/headless_runner.py` selects browser cohorts from provider configuration and delegates calls to the shared runner. Method lists, version pins, container images and binary paths belong to that configuration. Cohort overrides keep resource and ordinary browser experiments distinct; neither method labels nor a fixed participant count select runtime behavior. Version checks belong to `providers/runtime_identity.py` and run through `HeadlessProvider.validate()` for both specialized and generic entry points.

Formal pages cover rankings, datasets, methodology, results, provider descriptions and interface examples, environment, reproduction, and account setup. Source coverage and access restrictions are factual limitations, not a user-assistance workflow.

The report application owns full rules, interfaces and auditable records. Its tables retain local metric definitions and link to the matching methodology example. The interpretation article owns the argument, mechanism experiments, counterexamples and decision implications; it repeats only enough context to stand alone. The homepage provides quantitative overview and article access, not a second prose summary. Provider integration details belong with their interface examples, not a separate page repeating task boundaries.

## Browser trajectory replay ownership

Both browser tracks use `harness/environments/webarena/runtime.py` for site preparation, execution locks, login and viewport contracts, and `harness/environments/webarena/har.py` for network evidence. These modules do not import either track’s Agent or replay runner. Track runners own orchestration and verdicts.

`harness/environments/browser.py` owns container launch and CDP readiness. Callers select the image, port and resource limits explicitly. Resource sampling comes directly from `harness/resource_accounting.py`. The isolated public WebArena replay retains its own evaluator-compatible, redacted network recording contract under `harness/trajectory_replay/public_webarena/har.py`.

- `harness/trajectory_replay/` owns reusable action execution, deployment rebinding, official evaluation, evidence validation and report contracts. Files in `scripts/` only expose those stages as commands.
- A replay dataset is browser-neutral. Participant profiles bind the browser or engine, binary, backend, deployment, accepted catalog, ledger and publication target. Adding Moli or another browser reuses the same runner and creates participant evidence outside Git.
- The repository keeps the current compact dataset and correction provenance. Physical attempts, intermediate catalogs, repair queues and report bundles remain in `BROWSER_EVAL_DATA_HOME`; `current-report.json` binds the accepted external artifacts by relative path and hash.
- Canonical task definitions, evaluator semantics and corrections that apply to every runner belong in `lexmount/webarena-verified` on protected branch `lex-main`. `browser-eval` pins an immutable commit from that branch; release checks reject the ServiceNow repository, PyPI implementation or an unapproved side branch.
- `browser-eval` owns browser participants, deployment binding, trajectory qualification and replay, BrowserTool actions, reset/readiness receipts, evidence collection, retry ledgers, replay-only postconditions, reports and publication. Environment-specific references stay here with provenance instead of changing the canonical benchmark.
- A temporary evaluator compatibility patch may remain in `browser-eval` only while a proven generic fix is pending in the fork. It needs paired behavioral controls and must be removed after the pinned `lex-main` commit contains the fix.
