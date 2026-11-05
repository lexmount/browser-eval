Simple is the best; reject over-design.

# Code review

Use the [review guidelines](docs/references/review-guidelines.md) that OCR loads. Their shared
section is copied from `lexmount/ai-template-py` at the commit named in the first
line; change it there and copy it here. The Browser Eval supplement at the end is
maintained in this repository.

# Benchmark ownership

Browser Eval owns benchmark scoring and input tooling as well as execution, evidence and reports. Keep the independent scorer APIs in `src/lexbench_webfetch/` and `src/lexbench_websearch/`; they must work without browsers or provider credentials. Shared dataset distribution and pinning belong in `src/harness/benchmarks/`.

Hugging Face owns versioned public benchmark definitions and redistributable reference inputs. `src/harness/benchmarks/data-lock.json` selects their repositories, full commits and file hashes. Change a selection through `make pin-dataset DATASET=<track> REV=<full-HF-commit>`; keep import bindings aligned and historical snapshots intact. Do not copy datasets into Git or reintroduce retired standalone scorer repositories as execution dependencies. Official upstream evaluators retain their pinned code dependencies. Full third-party references without redistribution rights remain external approved inputs.

Every track records full source commits and exact task, reference and scorer hashes ([benchmark editions](docs/benchmarks.md#benchmark-editions)). Documentation links to selection owners instead of repeating current versions. WebArena and WCXB retain their upstream authorities.

# Language and names

Text the project writes has English as its source; text from outside keeps its original language byte for byte. `CONTRIBUTING.md#text-language` owns the rule. Preserve hash-bound acceptance/evidence artifacts. [Report languages](docs/report-hosting.md#report-languages) owns report generation: public report CLIs fall back to English, and a rebuild keeps the saved language unless the user asks otherwise.

Use `Web Extract` for this project’s structured field extraction track and its owned paths. Preserve vendor product names, API identifiers, industry terms, original source paths and quotations. Naming checks must distinguish project namespaces from source content.

Use `Browser Use` for the autonomous track and `Browser Trajectory Replay` for the fixed BrowserTool-action track. Keep these formal track names in English in every language, including Chinese documentation and discussion. Their implementation roots are `src/harness/browser_use/` and `src/harness/trajectory_replay/`; do not add aliases or compatibility paths using the retired names.

# Change lifecycle and repository footprint

- Extend the current owner before adding a parallel implementation. A second browser, cohort, dataset size, provider, language or release must select behavior through data, a registry or parameters in the existing pipeline. It must not create a browser-specific runner, copied renderer, numbered implementation or compatibility alias.
- Keep command-line files as thin entry points. Parsing, validation, execution, aggregation and publication contracts belong in the track's shared `src/harness/` module so every entry point uses the same rules.
- Git owns reusable code, hash bindings, provenance annotations and synthetic fixtures. Hugging Face owns task definitions and acceptance contracts; those already frozen in Git stay until superseded. Report-instance profiles and publication bindings belong alongside external evidence, selected explicitly by the caller. Review payloads and report prose already in `research/` are frozen legacy that current code may read; add no new ones.
- Persist provider calls outside Git under `~/.browser-eval/` (or `BROWSER_EVAL_DATA_HOME`). Measured attempts, generated reports, report prose and illustrations, review payloads, diagnostics, temporary queues and run-local manifests live under the configured data home or the pinned Artifact Site release; commit compact path/hash bindings instead. Git may also contain independent source evidence and public release metadata, but never measured outputs, execution manifests, provider artifacts, rerun directories, credentials or internal work logs.
- Keep one supported current implementation of each stage. Remove superseded generators, repair scripts, migrations and their exclusive tests in the same change once no supported entry point calls them. Retain a historical revision in Git only when a current versioned authority names it and reproduction requires its bytes.
- Preserve published reports and historical evidence, here and in corrections, unless the owner explicitly requests removal of superseded history through the current-only authority in `.agents/skills/benchmark-report/references/audit-and-revision.md`. Frozen hash-bound evidence that a current authority names must keep verifying.
- Treat a large diff as a design signal. Before adding files, identify the existing owner, the smallest reusable extension and the data that can stay outside Git. File or line counts alone are not release gates; unexplained duplicate stages, private evidence and unreferenced historical versions are.
- Finish structural changes with a repository inventory: search for references to removed entry points, list the supported entry points, verify that the instance profile binds every external artifact when you hold it, and run the behavior and release gates that exercise those public boundaries.
- Do not preserve a changed command under its old name when its inputs or meaning change. Restore the old contract or introduce the new behavior under the existing shared abstraction and migrate every caller atomically.
- Keep behavioral tests for supported build, package and publication boundaries; do not replace them with assertions about private helper structure.

# Execution and evidence

- For benchmark design, execution, scoring, result audit, report generation or release, read `.agents/skills/benchmark-report/SKILL.md`. Use `.agents/skills/benchmark-editorial/SKILL.md` additionally only for a new or substantially rewritten standalone interpretation article, official blog or public-account article.
- Run repository Python only through `make` targets or `uv run --frozen [--extra ...] python`. `.python-version`, `uv.lock`, the Makefile and CI are the runtime contract; never fall back to system Python.
- Web, dynamic, popular-site and document text use shared `score_attempt` orchestration and one task-result predicate. Task-specific requirements live in case configuration, not provider- or track-specific scoring branches. Binary acceptance and text diagnostics remain separate; missing reference coverage is unavailable, not zero or perfect.
- A proven wrong command, version, scorer, reference or timing is a correction: preserve the prior evidence, supersede the complete declared scope and disclose score, denominator and latency effects.
- A reference, scoring or acceptance correction must fix the shared cause, not patch stored verdicts or one provider. Add paired executable controls to `src/harness/benchmarks/scoring-regressions.json` where both error directions are plausible; historical controls remain in pinned dataset snapshots. Run controls through the public `score_attempt` and task predicate, and bind the root cause plus regression IDs in every new reference/scoring revision receipt. A revision without this provenance fails before evidence is changed.
- Aggregate only declared, hash-bound evidence files; reject symlinks and never publish worker directories, local paths or internal run trees. Every auxiliary and retry artifact named by a result must be materialized and hash-verified at the aggregate boundary.
- Independent result review is never automatic. Start it only after explicit user instruction; default to machine failures, and require a separate explicit full-review instruction before reviewing passes or the entire matrix. Publish the exact reviewed/total scope and accepted unreviewed pass count.
- Evaluation credentials come only from the repository `.env`. Artifact Site publishing is the sole user-scoped exception and never loads `.env`; [report hosting](docs/report-hosting.md#maintained-benchmark-releases) owns its token lookup. Keep `.env.example` current and publish only account requirements, official acquisition links and variable names.

# Reports

- Reports are optional. Generate and inspect HTML locally; local construction requires no publication account. Upload only when the user requests sharing and authorizes the selected host. Building, checking or rebuilding a report does not authorize publication.
- Select report instances explicitly with `--profile` (or an external `--authority-root` bundle for historical full-cohort formats). Store their matrices, publication targets and mutable version receipts outside Git alongside the frozen evidence. Each report and track owns its own publication binding; never borrow another report's target.
- Before report authoring, run `uv run --frozen python scripts/report_template.py <webfetch|trajectory-replay|browser-use|web-search|web-extract>` and inspect its renderer, version-pinned example and checks. Update the owning code and relevant skill when a repeated gap is demonstrated; keep report-specific findings outside instructions. Do not generate a disposable parallel renderer under a work directory.
- A new track must first gain a runnable synthetic example, evidence/detail behavior tests and a release check, then register those resources in `configs/report-templates.json`. `src/harness/lexbench/` is a declared historical adapter and is exempt.
- New or substantially rewritten report renderers must wire semantic presentation checks into their release gate, with negative controls for missing/wrong highlights, duplicate summaries, broken evidence targets and missing failure logs. Use `src/harness/reporting/presentation.py` or equivalent track-specific adapters. A written instruction or a route-count check is not proof that the UI requirement is enforced.

# Where the other rules live

| Scope | Guide |
| --- | --- |
| Every report: page structure, comparison tables, inventories, labels | `.agents/skills/benchmark-report/references/presentation-quality.md` |
| Every report: shared presentation contract, release gates, visual reader pass, release history | `.agents/skills/benchmark-report/references/report-and-release.md` |
| Report languages, hosting and publishing tokens | `docs/report-hosting.md` |
| Benchmark design and build-to-build timing | `.agents/skills/benchmark-report/references/evaluation-design.md` |
| Audits, reruns and corrections | `.agents/skills/benchmark-report/references/audit-and-revision.md` |
| Web Fetch, including WCXB and certified cohorts | `src/harness/web_fetch/README.md` |
| Live-URL browser comparisons and native callers | `src/harness/web_fetch/HEADLESS.md` |
| WebMainBench | `src/harness/web_fetch/WEBMAINBENCH.md` |
| Web Search | `src/harness/web_search/README.md` |
| Web Extract | `examples/web_extract/README.md` |
| Browser Use | `examples/browser_use/README.md` |
| Browser Trajectory Replay | `docs/trajectory-replay-protocol.md` |
| Public WebArena recording replay | `examples/trajectory_replay/public_webarena/README.md` |
| Lexbench historical adapter | `src/harness/lexbench/README.md` |

# Pull requests and releases

All code changes go through a feature branch and pull request. Never push directly to main. Do not merge unless the owner authorized it for that work. PR titles and commit subjects follow `CONTRIBUTING.md#pr-and-commit-titles`; a squash merge copies the PR title onto `main`.

Software release names, version increments and metadata must follow [Software releases](docs/releases.md). Version changes require an explicit human release request. Code synchronization, review fixes, tests and report rebuilds must not automatically bump package, benchmark or report edition numbers, rewrite frozen release catalogs, or create/push release tags. Reuse existing build versions and record source hashes; keep unreleased changes separate from a certified edition.
