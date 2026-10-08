# Architecture

`harness/record_io.py` owns UTF-8 JSONL framing across tracks. Finalized evidence
uses strict readers; live certification may ignore only an unterminated final
JSON fragment or incomplete UTF-8 character. LF and CRLF delimit records;
Unicode line separators inside strings remain part of the value. Readers do not
rewrite persisted bytes, and callers that append evidence parse their existing
byte snapshot through the same owner.

- Each track’s current profile selects its dataset, participants, evidence bindings and publication target. `evaluation/dataset.json` is the historical live-URL Web Fetch task set.
- Providers fetch content without scoring it. Credentials are expanded locally and never published.
- `metrics.py` and `dynamic_lists.py` evaluate persisted output. Task success depends on the task kind: body, resource, file or dynamic structure.
- `report_build.py` checks output hashes, stages selected evidence, then calls the sole renderer `report.py`. It replaces only a marked generated directory.
- `scripts/build_report.py` builds `reports/webfetch/` from an explicit persistent evidence directory. There is no older-report dependency or compatibility renderer.

The persistent evidence bundle's `execution.json` associates selected attempts with their actual runtime parameters and timestamps. Measurements retain original content hashes. Reference evidence retains its source and licensing scope; source capture alone does not authorize redistribution. Signed-in raw HTML and private account information are excluded.

Task-scoped Web Fetch execution belongs to `harness/web_fetch/benchmark_runner.py`;
`scripts/run_benchmark.py` is its command entry. An explicit `--provider-config`
selects one configuration for the requested task scope. Repository defaults
retain their separate webpage and resource configurations. Physical-call
artifacts receive hash bindings at collection. `artifacts.py` enumerates and
verifies those bindings before aggregation, rejects symlinks and collisions,
and never replaces a missing or mismatched binding with a newly computed hash.

Web Fetch evidence assembly belongs to `harness/web_fetch/assembly.py`.
`refresh_assembly.py` validates a predeclared refresh plan and uses the same
artifact boundary and speed-bundle copier. `speed_cohort.py` owns the complete
same-window latency matrix and summary. The corresponding `assemble_headless.py`,
`assemble_webfetch_refresh.py` and `package_headless_speed.py` scripts only invoke
these modules. Assembly preserves all physical-call outputs, auxiliary files and
public call logs, rejects symlinks and conflicting bytes, and exposes its new
output directory only after validation succeeds. Canonical source and list
references resolve through their hash-bound external-input manifest when present.
Reference-check observations are copied from each source batch's certificate by
path and SHA-256; adjacent notes and undeclared observations stay outside the
assembly. The same copier serves refresh assembly, reports, speed and resource comparison bundles.

`artifacts.py` also owns task-scoped source and list-reference copying for runners,
assembly and reports. It retains each selected task's source directory and matching
list reference, checks copied bytes, and excludes other tasks and top-level notes.
Task additions and replacements use their own input directories; canonical builds
use the canonical dataset's verified inputs. Custom input trees reject symlinks
before provider calls. Files within a selected task directory remain part of that
task's declared source scope; this scope does not grant redistribution rights.
Provider-blind speed selection belongs to `speed_cohort.py`; reference-freeze
computation, browser observations and promotion validation belong to
`reference_freeze.py`. Their command scripts expose the same public arguments,
and promotion tools import the shared module rather than another command script.

`reference_freeze.py` also owns post-call observation acquisition;
`evidence.py` owns live per-case reference certificate projection. Their command
files only invoke the shared owners. Both use `reference_lifecycle.py` for the
fresh-reference task population, provider coverage and acquisition-route
contract. Certificates are sidecars to the physical run, retain its exact
manifest and logical records, and appear only after finalization completes.
Resuming cannot reuse a certificate for changed calls or replace a failed check.

`evidence.py` owns provider append and complete-provider projection. Both verify
all physical-call artifacts and reference observations through `artifacts.py`.
Append retains existing JSONL bytes, updates declared method coverage, and swaps
the validated private bundle atomically. Projection creates a separate bundle
only after validation; its command scripts contain no execution or copy logic.
Assembly reads aggregate execution batches after append, while a complete physical
run awaiting postcheck retains the independent per-case certificate path.

Provider subsets use the same `assemble_headless.py --exclude-providers` boundary.
Assembly reuses an existing review only for unchanged retained attempt records;
it does not judge new or modified evidence. Timing corrections retain their
original selection protocol and diagnostic binding. A derived receipt binds the
retained unchanged attempts and exact output hash, with the source receipt hash.
Missing entries and non-finite, negative or boolean timing values are rejected.

`resource_cohort.py` validates the declared resource-method matrix, finite
measurements and common execution environment, and rebuilds the resource summary
from its original records. Assembly and report packaging share the comparison
evidence copier in `artifacts.py`; only declared outputs, call logs, reference
evidence and cohort metadata enter those directories. Public packages exclude
private response envelopes and result-review payloads. Resource table participants
come from the measured cohort rather than a fixed browser list.

WebMainBench cohort certification is owned by `harness/web_fetch/webmainbench_certification.py`; scoring and score verification by `webmainbench_scoring.py`; full-cohort aggregation by `webmainbench_assembly.py`; and shared result-matrix, variant-identity and physical-call checks by `webmainbench_evidence.py`. Their command files delegate to these modules. Evidence-copy boundaries reject file and directory symlinks as well as escaping paths.

`harness/web_fetch/certification.py` owns the default pre/post reference sequence
and WCXB cohort certification. It uses `reference_lifecycle.py` to select browser
observations from declared provenance and acquisition routes. Risk-control
execution shares `risk_control.py` with certificate validation. Origin-preserving
case certification shares `webmainbench_correction.py` with correction application;
TEDS revisions share `webmainbench_diagnostics.py` with revision validation.
Their command paths remain stable. New revision receipts bind the actual shared
implementation; retained revisions verify their own frozen source copies.

`webmainbench_latency_correction.py` owns the retained sample's latency
certification, application and validation. New cohorts retain a hash-bound copy
of the certifier captured before calls; consumers verify those bytes without
executing them or comparing them with today's command wrapper. Historical
certificates use the exact Git revisions and hashes in
`harness/web_fetch/data/webmainbench-latency-source.json`. All declared physical
calls require finite nonnegative timing, including unselected rounds. Host
observations must be available before measurement starts.

Complete WebMainBench comparisons assemble the baseline and every registry-declared variant directly from their certified cohorts. Participant counts come from profiles and the registry throughout assembly, rendering and release checks. Only declared evidence enters the package; assembly commits a new directory after all checks pass. Replacing a cohort creates a new package and retains the previous one. See [the WebMainBench guide](../harness/web_fetch/WEBMAINBENCH.md) for the supported commands and historical binding format.

Independent HTTP source capture and HTML extraction belong to `harness/web_fetch/source_capture.py`; its command is `scripts/collect_source_evidence.py`. Reference audits, scope refreshes and browser observations import the same extractor. `document_text.py` owns document extraction for both captured sources and provider responses, with explicit whitespace preservation for source reconstruction. Capture writes a new external evidence directory, validates the complete requested task scope before requests, and retains failed HTTP observations for review. Capture alone neither approves a reference nor certifies a provider result.

`harness/web_fetch/reference_inputs.py` resolves dataset-bound source inputs and the complete historical dataset. `evaluation/source-inputs.json` binds the dataset, its compact task catalog and every external input by SHA-256. Dataset readers preserve the exact original bytes; source readers verify the installed inventory before using it. Missing or changed inputs cannot fall back to a checkout copy or the non-executable catalog. Custom datasets without a manifest use their own adjacent inputs. Import and reconstruction commands are documented in [reference inputs](reference-inputs.md).

`harness/web_fetch/headless_runner.py` selects browser cohorts from provider configuration and delegates calls to the shared runner. Method lists, version pins, container images and binary paths belong to that configuration. Cohort overrides keep resource and ordinary browser experiments distinct; neither method labels nor a fixed participant count select runtime behavior. Version checks belong to `providers/runtime_identity.py` and run through `HeadlessProvider.validate()` for both specialized and generic entry points.

Formal pages cover rankings, datasets, methodology, results, provider descriptions and interface examples, environment, reproduction, and account setup. Source coverage and access restrictions are factual limitations, not a user-assistance workflow.

`providers/headless_worker.py` owns native browser execution and is included in
the wheel. `markdown.py` owns the pinned converter dependency and command; both
browser adapters use its packaged JavaScript asset. Resource images must match
the host worker hash before measured calls. Docker is loaded only for resource
execution. General CLI reports derive method descriptions from measured records;
profile-validated repository builds explicitly enable authored interpretation
and its bound supporting materials in the same report implementation.

The report application owns full rules, interfaces and auditable records. Its tables retain local metric definitions and link to the matching methodology example. The interpretation article owns the argument, mechanism experiments, counterexamples and decision implications; it repeats only enough context to stand alone. The homepage provides quantitative overview and article access, not a second prose summary. Provider integration details belong with their interface examples, not a separate page repeating task boundaries.

Historical WebMainBench latency certifiers are retained as immutable `.py.txt`
artifacts under `harness/web_fetch/data/latency-certifiers/`. The source registry
binds their SHA-256, original commit and path. Validation reads and hashes these
files; it never imports or executes them and does not require old Git objects.
The current certifier remains `webmainbench_latency_correction.py`.

## Lexbench comparison adapter

`harness/lexbench/` owns the retained Lexbench comparison adapter: report assembly,
resource matrices, saved-profile aggregation, evidence validation, charts and
static evidence packaging. `resource_profiles.py` owns the frozen Linux profile
queue, while `resource_matrix.py` owns the separate rotating-call experiment.
Their protocols retain distinct execution order and sampling contracts. Linux
collection and aggregation share one population validator; collection freezes
protocol and source bytes and stops the queue when either changes.
`reproduction.py` owns frozen layout command preparation
and execution; report bundles copy that same source. It verifies task scope,
input hashes and the captured runtime before execution, preserves existing runs
and rechecks inputs afterward. `verification.py` owns static validation, release
profile bindings and executable negative controls. Its rendering contract and the
package loader are installed library assets, with no runtime dependency on
`tests/`. Imports and help commands do not read report evidence.
Packaging rejects linked source directories and nested output, builds in a
temporary sibling directory and exposes the output only after every file and
catalog has been written successfully.
Its command scripts delegate to these owners. Benchmark-specific schemas and
frozen historical populations remain in the adapter; browser execution and Web
Fetch scoring retain their existing owners.

`harness/resource_calibration.py` pairs profiler-on/off calls and validates their
experimental provenance without importing code from evidence directories. It
retains the frozen upstream calculation and result schema, rejects partial or
duplicate pairing and invalid durations, and ships its Apache-2.0 attribution and
license. Saved runner files are hash-verified as provenance. Only an explicitly
requested measurement executes the declared external driver. Historical calls,
collectors and certificates retain their original bytes.

The adapter uses the existing `harness/web_fetch/safety.py` path boundary for
certificate entries, runner inputs, resource receipts and artifact directories.
Declared paths must be relative to their evidence root; absolute paths, parent
traversal and file or directory symlinks are rejected before reading. Task and
method identifiers used in report output must be safe filename segments. The
static verifier applies these same boundaries before opening task files, including
linked report roots and linked case directories. Base and partially extended
comparison reports keep the registered methods visible on home and interpretation
pages; a missing configuration does not authorize an unmeasured comparison.

## Browser trajectory replay ownership

Both browser tracks use `harness/environments/webarena/runtime.py` for site preparation, execution locks, login and viewport contracts, and `harness/environments/webarena/har.py` for network evidence. These modules do not import either track’s Agent or replay runner. Track runners own orchestration and verdicts.

`harness/environments/browser.py` owns container launch and CDP readiness. Callers select the image, port and resource limits explicitly. Resource sampling comes directly from `harness/resource_accounting.py`. The isolated public WebArena replay retains its own evaluator-compatible, redacted network recording contract under `harness/trajectory_replay/public_webarena/har.py`.

- `harness/trajectory_replay/` owns reusable action execution, deployment rebinding, official evaluation, evidence validation and report contracts. Files in `scripts/` only expose those stages as commands.
- A replay dataset is browser-neutral. Participant profiles bind the browser or engine, binary, backend, deployment, accepted catalog, ledger and publication target. Adding Moli or another browser reuses the same runner and creates participant evidence outside Git.
- The repository keeps the current compact dataset and correction provenance. Physical attempts, intermediate catalogs, repair queues and report bundles remain in `BROWSER_EVAL_DATA_HOME`; `current-report.json` binds the accepted external artifacts by relative path and hash.
- Canonical task definitions, evaluator semantics and corrections that apply to every runner belong in `lexmount/webarena-verified` on protected branch `lex-main`. `browser-eval` pins an immutable commit from that branch; release checks reject the ServiceNow repository, PyPI implementation or an unapproved side branch.
- `browser-eval` owns browser participants, deployment binding, trajectory qualification and replay, BrowserTool actions, reset/readiness receipts, evidence collection, retry ledgers, replay-only postconditions, reports and publication. Environment-specific references stay here with provenance instead of changing the canonical benchmark.
- A temporary evaluator compatibility patch may remain in `browser-eval` only while a proven generic fix is pending in the fork. It needs paired behavioral controls and must be removed after the pinned `lex-main` commit contains the fix.
