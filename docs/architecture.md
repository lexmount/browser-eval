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

`harness/web_fetch/result_review.py` owns review validation, score-only rebinding,
supplemental-review merging and installation. Its two revision commands are thin
entry points. Reuse requires unchanged task definitions and reviewer-visible
evidence; changed inputs require independently recorded supplemental judgments.
Installation validates the candidate before replacing the review, rejects linked
targets, and checks that evidence and the destination remain unchanged while
staging. These operations neither call providers nor start independent review.

`harness/web_fetch/review_execution.py` owns opt-in blind-review and later anomaly
investigation execution. Their command scripts delegate to this owner. A private
workspace receipt binds frozen dataset/results bytes, all declared artifacts,
reviewer configuration and the current review implementation. Resume validates
these bindings before changing packets or reusing outputs; anomaly grouping may
remap completed groups only within that bound context. Installation uses the
same validator and atomic writer as offline review revisions.

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
case certification shares `webmainbench_correction.py` with correction application
and offline scoring revisions. The full-population and certified-case revision
commands use the same scorer loader and metric adapter in `webmainbench_scoring.py`.
Revisions are staged before a new evidence directory becomes visible; retained
calls and source evidence are preserved. The superseded schema-v1 reference writer
is retired, while historical receipt validation remains supported.
TEDS revisions and their integrity audits share `webmainbench_diagnostics.py`
with revision validation. Both audit and validation check the complete declared
method-by-task matrix. Audits use the shared aggregation rules, including metrics
with no applicable samples, reject non-finite scores and install a receipt only
after checking the evidence again.
`safety.py` owns evidence-tree identities and staging for new offline revision
directories. TEDS and full-population scoring revisions use this boundary; reference
revisions use the same tree identity checks. A failed revision stays private, and
a changed source prevents installation. TEDS revisions retain the first official
aggregate alongside the first official per-result scores and bind the declared
scorer commit and copied shared implementation in their receipts.
Installed scoring registries are located through distribution records in
`storage.py`; they do not assume the interpreter and package share a prefix.
The package gate runs both base and WebMainBench-extra installations outside the
checkout, including the complete offline TEDS revision and audit path.

`wcxb_report_check.py` owns the WCXB release checks; the checker command, common
report commands and publisher call this shared module. An installed checker takes
`--authority-root` to read the repository's bound WCXB profile. Report editions
share the route-inventory contract in `harness/report_languages.py`: Chinese-only
builds require the English notice, while explicitly generated English requires a
complete matching inventory. Locale-specific number spacing does not change the
measurement comparison.
Optional revision registries are resolved when a revision loads them; importing
the ordinary offline harness does not require those external data files.
Their command paths remain stable. New revision receipts bind the actual shared
implementation; retained revisions verify their own frozen source copies.

`webmainbench_report_check.py` owns both the frozen sample and complete-cohort
release checks. The sample and full commands select their respective check;
common report commands call the sample check directly. Both accept an explicit
`--authority-root` for repository profiles, release records and publication
bindings when run from an installed package. Frozen sample-specific requirements
remain separate from the complete cohort's participant registry and bindings.

WebMainBench authored content is external data. The sample's
`evaluation/webmainbench-content-binding.json` binds `report-content.json` under
`BROWSER_EVAL_DATA_HOME/report-assets/webmainbench-sample/`, the frozen sample
manifest and the exact dataset, results, score summaries, execution and timing
receipts. Its `source` identifies the pinned Artifact Site version and export
manifest hash for restoration. Obtain that version's export with the existing Artifact
Site authorization, then run:

```sh
uv run --frozen python scripts/restore_report_content.py \
  --binding evaluation/webmainbench-content-binding.json \
  --export /absolute/path/to/pinned-export.zip \
  --output /absolute/data-home/report-assets/webmainbench-sample/report-content.json
```

Recovery verifies the manifest, virtual blocks, source pages and final content
hash before creating the new content file. It never replaces an existing file.
The archive hash records the original download; ZIP timestamps may change on
another export without changing the bound manifest or virtual files.
Do not substitute the latest report. Public privacy projections retain
original file identities; projected execution metadata cannot replace the private
source bytes needed by a rebuild.

`harness/reporting/assets.py` validates authored content for both sample and full
builds. Sample release checks also compare the rendered main content with the
bound data. Changed evidence requires newly reviewed content and a new binding;
publication-target changes alone do not change the frozen sample authority.
The renderer's unbound mode derives its explanation from the supplied results
without historical prose or version-specific conclusions. Translation sources
remain external; only explicitly selected editions are rendered.

`dynamic_cohort.py` owns measured dynamic-supplement assembly and append. Both
stages use the shared artifact boundary for primary outputs, auxiliary files,
retained retry evidence, private artifacts and reference observations. Assembly
exposes a new directory only after copying succeeds; append retains its rollback
boundary. Original JSONL record bytes and scoring values remain unchanged.
Dynamic, ordinary and resource-cohort packaging share execution metadata
projection in `publication_privacy.py`. It omits hostnames and local source
directories, marks omitted paths and retains recorded file names, safe relative paths
and hashes. It never substitutes a repository configuration for a measured run
or infers missing hashes. Each batch retains its declared runtime identity;
conflicting configuration hashes are rejected. An undeclared adjacent identity
file cannot replace that identity or become evidence for another batch.
`risk_control_dataset.py` owns the frozen risk-control definition compiler. Its
command accepts an explicit definition directory and output; importing it or
requesting help does not read historical inputs. Task rules and source hashes
retain their frozen authority.

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

`harness/web_fetch/wcxb_scoring.py` owns certified WCXB scoring through
`scripts/score_wcxb_results.py`. Its explicit profile selects the ordered
participant matrix; certified binary identities select any paired timing roles.
The scorer verifies source, execution, logical and physical-call evidence before
installing derived scores. It stages retained retry responses privately and
guards consumed inputs through installation. Existing scores are immutable;
handled installation failures remove only outputs created by that operation.

`harness/web_fetch/report_check.py` owns the shared report acceptance entry,
`scripts/check_report.py` and `scripts/check_report_revision.py`. Both delegate
WCXB and WebMainBench to their maintained validators; the shared owner checks
the legacy Web Fetch evidence, inventory and generated
pages itself. `--authority-root` selects the repository containing reviewed
profiles and inventories; `--profile` stays under that repository's `evaluation/`
directory. Checks remain active with Python optimization enabled. Manifest-bound
editorial image parity belongs to `harness/reporting/article_assets.py` and is
loaded only when an English edition exists.

`harness/web_fetch/reference_inputs.py` resolves dataset-bound source inputs and the complete historical dataset. `evaluation/source-inputs.json` binds the dataset, its compact task catalog and every external input by SHA-256. Dataset readers preserve the exact original bytes; source readers verify the installed inventory before using it. Missing or changed inputs cannot fall back to a checkout copy or the non-executable catalog. Custom datasets without a manifest use their own adjacent inputs. Import and reconstruction commands are documented in [reference inputs](reference-inputs.md).

`harness/web_fetch/headless_runner.py` selects browser cohorts from provider configuration and delegates calls to the shared runner. Method lists, version pins, container images and binary paths belong to that configuration. Cohort overrides keep resource and ordinary browser experiments distinct; neither method labels nor a fixed participant count select runtime behavior. Version checks belong to `providers/runtime_identity.py` and run through `HeadlessProvider.validate()` for both specialized and generic entry points.

Native repository adapters also use `providers/runtime_identity.py`. Their
contracts declare mandatory identity fields and implementation paths, while
configuration supplies the expected Git, Node and package-manager versions.
Preflight checks the actual clean checkout and executables against those values.
The DeepSeek worker's deployed copy must match the repository worker byte for
byte. Failed revalidation removes any previously cached identity. Changing
version pins therefore requires a matching declared configuration, without editing
the adapter or changing historical evidence and certificates.

CowAgent uses the shared JSONL subprocess transport. Each provider instance
loads its configured checkout in a separate worker process, preserving upstream
tool behavior while avoiding Python module-cache collisions between versions.
The worker source and Python executable are part of runtime identity. Upstream
prints go to the retained diagnostic stream; only worker responses enter the
JSONL protocol. The shared transport enforces deadlines and cancellation.
Each process retains its own response queue. Diagnostic readers from an earlier
process cannot append to a restarted process's log window.
The first call retains startup diagnostics; later calls retain only their own
log slice. Startup exits and closed input pipes keep the original transport
error together with stderr and the process exit code.
Standalone worker files live in `harness/web_fetch/workers/` and ship with the
wheel. Keeping them outside the adapter directory prevents direct Python
execution from shadowing standard-library modules such as `http`.

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

Offline scoring revisions use `harness/web_fetch/rescoring.py` and
`harness/web_fetch/scoring_revision.py`; their commands only parse arguments and
invoke those owners. Both run-level and assembled-evidence rescoring verify
hash-bound primary responses, auxiliary files, downloads, retained retries and
call logs before changing persisted scores. `result_review.py` owns review
identity, validation, verdict-neutral rebinding and superseded-review archives.
A score change cannot reuse a review whose inputs or machine verdict changed.

`reference_revision.py` owns reference-only corrections. It validates prior
revision bindings and acquisition artifacts, stages the complete change, copies
only selected task references from their bound input tree, and uses the shared
`safety.py` promotion boundary. The same boundary serves provider and dynamic
cohort append. Failed promotion restores the predecessor; failed recovery keeps
its original directory. Successful reference corrections retain their complete
predecessor in a private sibling `.NAME-reference-history-*/original` directory.
These predecessors are outside the current bundle and public package.

The scoring regression registry remains authoritative at
`evaluation/scoring-regressions.json`. Distribution builds install those exact
bytes under `share/browser-harness-eval`, allowing installed offline revisions
to validate provenance without a repository checkout or a duplicate registry.

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

## Report packaging and publication

Web Fetch lazy publication packaging belongs to
`harness/web_fetch/report_package.py`, exposed by `scripts/package_artifact_hub.py`.
Its loader is an installed JavaScript resource. Packaging retains privacy
projection and byte-pack downloads, validates the declared sampled matrix rather
than a dataset name or fixed case count, and checks links even under optimized
Python. The ZIP is staged outside the report and installed only after source and
destination identities still match; failed builds retain the previous archive.
Packaging does not replace the report's full release checks.

Web Fetch submission, bundle checking and remote verification belong to
`harness/web_fetch/report_publish.py`, `publication_check.py` and
`publication_verify.py`. Their scripts parse through these owners and do no work
on import. Submission selects the service from an explicit profile, checks any
requested host against it, and freezes the archive, source and profile before
upload. Both one-shot and staged uploads retain an operation journal, reuse its
idempotency key after interruption and install private receipts atomically.
Receipts distinguish submitted bytes from verified public output. Remote metadata,
public-file and export verification remain explicit read-only commands.

The bundle checker covers the complete physical ZIP and virtual-file inventory,
including duplicate names, unused blocks, pack gaps and trailing bytes. The
installed loader must match exactly; runtime assets receive the public sensitive
data scan. Checks on original scored files use contained, non-symlink paths.

Use `scripts/publish_artifact_hub.py --update --profile /absolute/profile.json`
with the corresponding `--archive`, `--source` and `--receipt`. A first creation
uses `--create`, an unbound profile with an explicit `artifact_hub_base_url`, and a
new receipt path. Keep `--operation-journal` until the command finishes. The
existing no-action mode reuses a bound receipt without uploading or downloading
report files. `scripts/verify_artifact_hub.py --slug SITE --profile PROFILE` reads
metadata; `--public-files --expected-archive ZIP` or
`--export --expected-archive ZIP` performs the respective byte verification.

Report publication belongs to `harness/browser_use/report_publish.py` and
`harness/trajectory_replay/webarena_report_publish.py`; their commands only parse
and delegate through these owners. Both upload a private, byte-exact archive
snapshot and guard its original archive, attestation and publication authority
before remote writes and final local binding. Full exported ZIP inventories must
match the frozen archive, including unique paths. Browser Use excludes its local
`.generated-report` rebuild marker from the upload while retaining it in the
guarded source tree. Its publication title comes from the current profile and
its preview uses the validated same-origin share token.

Both publishers keep private upload-operation and binding journals. Retries reuse
the original idempotency key and recover completed uploads before submitting
again. Browser Use also keeps multipart bytes stable across retries. Before local
binding, the publisher checks the remote version again. Profile and attestation
updates form a recoverable transaction bound to their paths and preceding hashes;
recovery refuses to overwrite independently changed files. A binding-phase receipt
lets a retry finish local bookkeeping without uploading another version. Keep
these journals and receipts until recovery finishes; `--operation-journal` and
`--binding-journal` select their locations when running outside the checkout.

Report packaging belongs to `harness/browser_use/report_package.py` and
`harness/trajectory_replay/webarena_report_package.py`. They share
`harness/reporting/publication.py`: freeze report and authority inputs before
release gates, stage the ZIP and attestation, verify the archive against the
frozen file inventory, and reject input or destination changes before installing.
Handled installation errors restore the preceding files; publishers reject an
interrupted installation through the existing archive hash binding. Browser Use
accepts `--authority-root` for repository-external execution. Replay build,
packaging and publication share dataset binding in `report_inputs.py`.

Visual release checks belong to `harness/browser_use/report_visual.py` and
`harness/trajectory_replay/webarena_report_visual.py`. Both use
`harness/reporting/visual.py` for browser discovery, input hashes, complete
viewport/theme coverage and atomic receipt installation. Current receipts bind
the report tree, checker sources, browser bytes and every rendered state; partial
or historical receipts require a new check. Source changes during rendering
prevent installation. Browser Use serves evidence over local HTTP, and both
tracks include success/failure details and the absent-English notice in their
visual scope. The original tree-digest encodings remain unchanged.

`harness/browser_use/report_check.py` and
`harness/trajectory_replay/webarena_report_check.py` own their report integrity
and semantic checks; command files and packaging call these same owners.
Both reject linked report trees before reading artifacts. Browser Use additionally
rejects linked source evidence and accepts `--authority-root` for its repository
profile, default selection, bound failure-cause review and remediation inventory.
An installed checker requires that explicit authority; it does not search the
working directory for another profile. Replay validation reads the public
bundle's own manifest and evidence. These checks neither run browsers nor change
measured results.

Both browser tracks use `harness/environments/webarena/runtime.py` for site preparation, execution locks, login and viewport contracts, and `harness/environments/webarena/har.py` for network evidence. These modules do not import either track’s Agent or replay runner. Track runners own orchestration and verdicts.

`harness/environments/browser.py` owns container launch and CDP readiness. Callers select the image, port and resource limits explicitly. Resource sampling comes directly from `harness/resource_accounting.py`. The isolated public WebArena replay retains its own evaluator-compatible, redacted network recording contract under `harness/trajectory_replay/public_webarena/har.py`.

- `harness/trajectory_replay/` owns reusable action execution, deployment rebinding, official evaluation, evidence validation and report contracts. Files in `scripts/` only expose those stages as commands.
- The selected-task remote pipeline uses `remote_runtime.py` to freeze deployment-bound inputs, `remote_execution.py` to run and certify a method cohort, and `webarena_results.py` to aggregate certified cohorts. All three stages validate the same nonempty, unique integer task selection. The runner binds static inputs before preflight and site reset, rejects changes before browser launch, and rechecks the measured reference window afterward. Certification records execution completeness independently of task success; diagnostic subsets cannot become a formal complete cohort. The commands retain the frozen protocol schemas and historical populations. Freezing accepts an explicit reference root for relative source revisions; execution accepts an explicit selection file and requires the configured WebArena checkout and CowAgent environment.
- `catalog_runner.py` executes declared replay subsets with the same action, evaluator and evidence contracts as catalog cohorts. Candidate revisions and portable actions use explicit bound inputs. The frozen original-failure adapter delegates to `remote_runtime.py`; stabilization declarations reconstruct the complete qualified parent and verify its byte hash for every task. They cannot borrow qualification for undeclared action or metadata changes.
- Public WebArena repair authoring belongs to `public_webarena/diagnostics.py`; `qualification.py` owns proposal-to-probe and proposal-to-batch derivation. The command scripts expose those owners. Version 2 authoring packets keep raw evaluator attempts in program-only provenance, remove that provenance from the model packet, and validate the full sanitized payload against its source. Repair jobs bind their configured model, reasoning effort and input hashes; failures retain terminal receipts. Derived suites validate before immutable publication, and proposals do not change qualification or measured scores.
- Public WebArena report staging, semantic checks and packaging belong to `public_webarena/publication.py`. Its `build` API requires an explicit authority profile, so installed callers can package externally stored evidence without a source checkout. `public_webarena/release.py` coordinates the repository's publishing commands with an explicit project root. The three command scripts only select these entry points; they do not import one another.
- The public WebArena package includes its compact benchmark catalogue, adoption evidence and structured release records. `harness/distribution.py` resolves declared distribution files across installer layouts; Web Fetch scoring controls use the same resolver. Measured evidence and the publication authority remain explicit external inputs.
- Qualification queues, repair and evidence revalidation suites, final adjudication and application share `public_webarena/qualification.py`. They reject duplicate task inventories and validate derived suites before exposing immutable outputs. Attempt projections must agree with their raw evidence in both verdict directions; missing legacy source evidence requires revalidation. Repaired trajectories cannot change the task instruction, evaluator or environment. Applying decisions returns a new suite so failed validation cannot leave partial edits in a caller's input.
- The isolated public WebArena track keeps shard planning, two-pass qualification orchestration, verified resume, correction queues and derived candidate suites in `public_webarena/qualification.py`. Its command scripts delegate to that owner. A dry run leaves existing evidence in place. Resume rejects linked artifacts, execution checks the frozen suite and scope, and derived suites are validated before an immutable output is exposed. Rainbond runtime gateway updates retain their existing reset-receipt authority. Qualification outcomes and their acceptance rules remain separate from browser comparison scores.
- Remote mutation diagnostics compile declared readiness and completion rules in `webarena_plan.py`; `remote_execution.py` owns proposal checks, execution, cleanup and evidence. Proposal emission and checking make no browser calls. Execution preserves the canonical actions and can retry only a verified compiled final observer, under a bounded policy that never repeats the mutation. Frozen inputs are checked before browser creation and after execution; setup, cleanup and input failures retain a terminal diagnostic receipt. These results remain diagnostic and cannot promote browser comparison scores.
- A replay dataset is browser-neutral. Participant profiles bind the browser or engine, binary, backend, deployment, accepted catalog, ledger and publication target. Adding Moli or another browser reuses the same runner and creates participant evidence outside Git.
- The repository keeps the current compact dataset and correction provenance. Physical attempts, intermediate catalogs, repair queues and report bundles remain in `BROWSER_EVAL_DATA_HOME`; `current-report.json` binds the accepted external artifacts by relative path and hash.
- Canonical task definitions, evaluator semantics and corrections that apply to every runner belong in `lexmount/webarena-verified` on protected branch `lex-main`. `browser-eval` pins an immutable commit from that branch; release checks reject the ServiceNow repository, PyPI implementation or an unapproved side branch.
- `browser-eval` owns browser participants, deployment binding, trajectory qualification and replay, BrowserTool actions, reset/readiness receipts, evidence collection, retry ledgers, replay-only postconditions, reports and publication. Environment-specific references stay here with provenance instead of changing the canonical benchmark.
- A temporary evaluator compatibility patch may remain in `browser-eval` only while a proven generic fix is pending in the fork. It needs paired behavioral controls and must be removed after the pinned `lex-main` commit contains the fix.
