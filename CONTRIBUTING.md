# Contributing

Simple is the best; reject over-design.

PRs are welcome for adapters, execution fixes, tests, documentation and translations. Public task definitions are versioned on [Hugging Face](https://huggingface.co/Lexmount); scoring code is maintained here; see [benchmark boundaries](docs/benchmarks.md). Run the [offline example](README.md#quickstart), then open the guide for the track you want to change.

## Report a bug

Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md).
For a new capability, use the [feature request template](.github/ISSUE_TEMPLATE/feature_request.md).
Search existing issues first and add evidence to a matching issue.

Open one issue per independently fixable problem. Give it a concrete title naming
the affected behavior, such as "CLI exits successfully when the input file is missing."
Start with a short description of the failure and its effect on the caller.
Then provide:

1. A minimal, complete command or code sample, its input and any setup it needs.
2. Concrete expected content or fields, the literal actual response/error excerpt,
   and a pass/fail condition for the same input. Counts alone are insufficient.
   Label illustrative output, and separate current observations from saved evidence.
3. The relevant version, method/provider, environment and observation date.
   For hosted services, include the endpoint, parameters and request ID when available.
4. Supporting evidence and a diagnosis or proposed fix, if established.

For benchmark findings, identify the task, output scope and acceptance rule.
A richer baseline response does not make its extra content a requirement: request
only the main content or resources the task needs. Keep media discovery and file
delivery separate from ordinary body extraction unless explicitly required.
Distinguish a tool
failure from a reference, scorer or adapter defect. Label saved-call evidence with
its original date; it does not establish that the current service still fails.
State which cause is demonstrated by the evidence and which explanation remains
a hypothesis. A reporter does not need to know the root cause to report a bug.

Keep the reproduction and expected/actual results readable without opening a
report. Put long logs, traces, offline tests and supplementary cases in linked
files or collapsed `<details>` sections. Paste code and errors as text; diagrams
and screenshots supplement them. Keep a primary explanatory diagram visible near
the opening; do not fold the visual needed to understand the failure. Remove keys,
cookies and session data.
A tracking issue should contain a short goal and links to the individual issues.
For findings from an evaluation, use the [evaluation task template](.github/ISSUE_TEMPLATE/evaluation_task.md),
set the issue type to Task and link the report that defines the batch. Attach each
finding as a native sub-issue; keep different reports or releases in separate tasks.
Order fixes by the declared task objective. Give deferred capabilities their own
batch, and label any task-family subset without replacing the full report counts.
Include a concise failure breakdown with denominators, representative behavior
and related issues. State whether categories overlap, and keep observed failure
counts separate from proven causes; examples alone do not establish prevalence.
Close an issue or check an item only after verifying its original failure, with
the tested version and evidence recorded. Move a newly discovered problem to its
own issue instead of changing what a historical issue means.

When filing in another repository, follow its template and terminology. Before
submitting, read the issue as a maintainer: can someone unfamiliar with the
investigation understand the failure and reproduce it from the supplied material?
Check the posted body, attachment links and code formatting after submission.
For CLI submissions, inspect the installed command's help before assuming which
creation or attachment options it supports.

These templates follow the reproduction-first approach used by
[Playwright](https://github.com/microsoft/playwright/blob/main/.github/ISSUE_TEMPLATE/bug.yml),
[Scrapy](https://github.com/scrapy/scrapy/blob/master/.github/ISSUE_TEMPLATE/bug_report.md)
and [Requests](https://github.com/psf/requests/blob/main/.github/ISSUE_TEMPLATE/Bug_report.md).

## Make a change

| Change | Where it belongs |
| --- | --- |
| Browser or provider adapter | Existing execution modules; see the [provider contract](docs/provider-contract.md) |
| LexBench-Web-Fetch task requirements | Hugging Face task definitions, applied to every method |
| Scoring fix | Shared scorer, scenario regressions and a recorded correction reason |
| New browser, version or sample size | Configuration or registry; reuse the runner and renderer |
| New report instance | An external instance profile, evidence and report content; reuse its build and release commands |
| Reusable logic | `src/harness/`; `scripts/` stays a thin command entry point |

Keep measured counts, host facts and report-specific interpretation out
of shared renderers: read them from the selected profile and bound evidence.
Published historical reports retain their own content and provenance.

Use absolute Python imports and keep ordinary imports at the top of the module. Existing scorer modules retain import spelling required by published source-hash receipts; use absolute imports in new modules.
Keep an import local when loading an optional dependency, preparing a runtime
module, avoiding a circular import or preserving test isolation.

Test both accepted and rejected scenarios. Fix scoring rules rather than editing saved verdicts. Refactoring must preserve historical measurements and certificates.

Keep the [Chinese README](docs/README.zh-CN.md) aligned when setup or usage changes. Additional README translations are welcome; other docs do not need duplicate translations. Reports follow their track's language settings.

Benchmark changes follow [benchmark editions](docs/benchmarks.md#benchmark-editions).

### Text language

The test for any string: did the project write it, or did it come from the web or a dataset?

- **The project wrote it.** Identifiers, comments, docs, logs, errors, catalog keys and report interface text have English as their source. The Chinese report edition comes from language catalogs, selected through the report language contract.
- **It came from outside.** Dataset queries and tasks, frozen web pages, human references, measured responses and quotations keep their original language byte for byte. Never translate, normalize or re-punctuate them; language checks must exempt them.

Test fixtures that stand in for outside text keep its language. Frozen acceptance and audit artifacts are hash-bound experiment inputs whoever wrote them; see [reference inputs](docs/reference-inputs.md).

## Update Web Fetch dependencies and methods

Select a published dataset with `make pin-dataset DATASET=<track> REV=<full-HF-commit>`.
It rejects commits outside the dataset's `main` history and verifies the archive and file hashes before updating the data lock and import bindings. Scorer changes belong in this repository and use the existing scenario regressions.
Historical report certificates retain their original evaluator identities.

Register Web Fetch products in `src/harness/web_fetch/data/report-methods.json`;
executable versions and run parameters come from saved evidence. See
[method metadata](src/harness/web_fetch/README.md#report-method-metadata) and
[adding a method or release](src/harness/web_fetch/README.md#adding-a-method-or-release).

## PR and commit titles

Use `<type>(<scope>): <summary>` for PR titles and new commit subjects. The scope is optional; use the module or feature affected, such as `scoring` or `webfetch`.

Choose the type that describes the change:

| Type | Change |
| --- | --- |
| `fix` | Correct existing behavior |
| `feat` | Add supported behavior |
| `docs` | Change documentation |
| `refactor` | Restructure code without changing behavior |
| `test` | Add or correct tests |
| `perf` | Improve performance |
| `build` / `ci` | Change packaging, dependencies or automation |
| `chore` | Other maintenance |

Write the summary in English, start with an imperative verb, and describe the concrete change. Aim for 72 characters or fewer, including the prefix; omit a trailing period. Use `!` before the colon for a breaking change and explain the migration in the description.

Examples:

- `fix(scoring): require facts in the returned body`
- `fix(webfetch): disclose build provenance for partial reruns`
- `docs(contributing): define PR and commit title conventions`

Avoid titles such as `Fix review feedback`, `Update code` or `Misc improvements`; name the behavior or document that changes. A PR title describes the complete diff; each commit subject describes that commit. Update the PR title when its scope changes. If squash merging is used, use a subject that follows the same format. Apply this convention to new work; do not rewrite shared commits solely to rename them.

## Run the checks

```bash
make setup-test
make ci
```

Development checks use the Python version in `.python-version` (3.11) and Node.js 22. The base example does not need Node.js. `make ci` also needs [ShellCheck](https://github.com/koalaman/shellcheck#installing) and downloads Chromium through Playwright. `make help` lists the targets; `make clean` removes build outputs and caches; `make format FILES="path.py ..."` formats selected files only, because Ruff formatting is not applied repository-wide. Text file reads and writes name `encoding="utf-8"`; `tests/test_explicit_utf8_io.py` enforces this and lists the hash-bound sources that must wait for their next authorized revision.

Use focused checks while editing:

```bash
make docs-check
make test-python TEST_PATHS=tests/web_search
make test-python TEST_PATHS=tests/test_scoring_regressions.py PYTEST_ARGS='-k reference'
make test-python PYTEST_ARGS='-n 4 --dist worksteal --durations=20'
```

`TEST_PATHS` selects files or directories; `PYTEST_ARGS` adds pytest options;
`PYTEST_ARGS='-m "not slow"'` skips the slowest tests while iterating, and CI still runs them.
Without `TEST_PATHS`, Python tests cover the entire suite. `make docs-check`
checks README navigation, citations, track naming and source-language policy;
it needs neither Node.js nor downloaded benchmark data.

CI splits Python tests by a stable hash of their node IDs across two runners,
with four workers per runner. Browser-test modules run once in `browser-check`;
static, Node and fixture checks run once. Local
`make ci` remains serial unless `PYTEST_ARGS` requests workers; focused checks
are usually enough while editing, followed by the complete checks before release.

CI uses this documentation check for PRs changing only the root README,
contribution/security guidance, `AGENTS.md`, Markdown under `docs/`, or
PNG/JPEG/WebP/GIF images under `docs/assets/`.
Code, test, dependency, dataset-card and unknown paths keep the full
test, installation and browser gates. Main pushes and tags always run all gates.
Checks include every commit in the PR; a final documentation commit does not
reduce the scope of earlier code changes. The required `Python 3.11` status
rejects failed, cancelled or unexpectedly skipped checks.

| Check | What it covers |
| --- | --- |
| `make lint-check` | Basic Python errors, loop-variable closures and unchecked `zip` across the repository, and the configured Ruff rules for the packages in `[tool.ruff] include` |
| `make package-check` | Base CLI, then source archive → wheel → base/optional installation outside the checkout (`src/harness/package_gate.py`) |
| `make shell-check` | ShellCheck on `deploy/` scripts |
| `make browser-check` | Chromium reference capture, report interactions and Web Search visual checks |
| `make test-reference-inputs` | Strict historical-input checks after importing [approved reference copies](docs/reference-inputs.md) |
| Track release gate | Evidence, report presentation and browser checks before packaging or publishing |

`make ci` runs what CI runs on a code change: `ci-tests`, `package-check` and `browser-check`. It runs without historical pages: only tests requiring absent historical inputs are skipped. Corrupt installed inputs fail; strict reference mode also fails on missing or incomplete sets.

`tests/test_golden_reports.py` builds each report track's synthetic example and compares every route with the SHA-256 in `tests/golden/`. Any change to rendered output fails it. If the change is intended, regenerate the snapshots and commit them with the change, so reviewers see which routes changed:

```bash
UPDATE_GOLDEN=1 uv run --frozen --all-extras python -m pytest tests/test_golden_reports.py
```

To see the changed lines, set `GOLDEN_KEEP=<directory>` on two revisions and compare the kept builds with `diff -r`.

For report releases, follow the [release gates](.agents/skills/benchmark-report/references/report-and-release.md#release-gates). Real services need the environments listed in their track guides.

<details>
<summary>What the package gate tests</summary>

The gate builds a source archive, then a wheel from that archive. Standard `python -m pip` installs both formats in a clean virtual environment outside the checkout, checks dependencies with `pip check`, and runs the installed command before the wheel smoke tests. It compares installed modules and resources byte for byte and exercises TypeScript tool bridges, synthetic TEDS revisions, LangChain extraction and Crawl4AI downloads against local HTTP fixtures, including HTTP failures. Missing, stale or extra installed files fail the gate. Node tests use `node:module.stripTypeScriptTypes`.

</details>

## Keep private data out of Git

Do not commit `.env`, tokens, cookies, provider outputs, run directories, generated reports or private reviews. Keep third-party sources, versions and licenses. A task definition does not grant permission to redistribute its source page.

<details>
<summary>Maintainer reference: source exports and historical inputs</summary>

A current-source export excludes old commits that may still contain externalized material. Review development history before distributing it; deleting a current file does not remove its earlier copies.

```sh
git archive --format=tar.gz --output=/tmp/webstone-source.tar.gz HEAD
```

The archive excludes `.git`, untracked files and local installations. Extract it, follow README setup and run `make ci` with an empty `WEBSTONE_DATA_HOME`. Historical certifier sources retained by manifests are read-only, hash-bound text inputs, not executable entry points or dependencies on old Git commits. See [third-party content](THIRD_PARTY.md) and [reference inputs](docs/reference-inputs.md).

</details>

## License

Contributions use the [Apache License 2.0](LICENSE). Contributors retain copyright in their contributions; submitting a contribution grants the applicable license and does not transfer ownership to LexMount. Preserve existing copyright notices and third-party terms described in [THIRD_PARTY](THIRD_PARTY.md).

## Software releases

Follow the [release guide](docs/releases.md) for software compatibility, version changes and release checks. Report builds have their own identities; the Webstone version names the Web Fetch benchmark edition. `AGENTS.md` owns when a version may change.
