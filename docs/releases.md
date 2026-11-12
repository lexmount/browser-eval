# Software releases

Webstone versions the execution and reporting software. Software tags use `vMAJOR.MINOR.PATCH` and identify one tested commit.

## Version rules

Follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The public compatibility contract covers documented CLI commands and options, dataset/provider inputs, and reading saved evidence through supported report builders. Internal Python helpers and generated HTML markup are not public APIs.

| Change | Before 1.0.0 | From 1.0.0 onward |
| --- | --- | --- |
| Bug fix or documentation that cannot change any score | Patch: `0.4.0` → `0.4.1` | Patch: `1.0.0` → `1.0.1` |
| Change that can change a Web Fetch score with the same tasks: a scoring rule, reference check or acceptance check (including a bug fix), or a new dataset pin with corrected references | Minor: `0.4.1` → `0.5.0` | Minor: `1.0.1` → `1.1.0` |
| Compatible new adapter, command, track or optional input field | Minor: `0.4.1` → `0.5.0` | Minor: `1.0.1` → `1.1.0` |
| New dataset pin that adds or removes tasks, or changes their requests, output scope or routes | Minor: `0.5.0` → `0.6.0`; describe the break | Major: `1.1.0` → `2.0.0` |
| Remove or rename a public command, require incompatible inputs, or stop reading supported evidence without migration | Minor: `0.5.0` → `0.6.0`; describe the break | Major: `1.1.0` → `2.0.0` |

### Web Fetch benchmark edition

Webstone's [data lock](../src/harness/benchmarks/data-lock.json) selects the LexBench-Web-Fetch dataset commit, and its code holds the scoring rules. One Webstone version therefore names the whole Web Fetch benchmark edition. The table above decides the bump; a change that can move a score is never a patch.

The dataset keeps its own HF tag (for example `v2.2.0`) for its tasks and reference data. Reference data lives in the dataset; the code that checks answers against it lives here.

`uv run --frozen lexbench-webfetch version` prints the version and the scoring-source hash. When the scoring code differs from what that version shipped, it prints `X.Y.Z+unreleased` with `status: unreleased`. Each release records its shipped hash in `RELEASED_SCORERS` in `src/lexbench_webfetch/versions.py`. A `lexbench-webfetch release --benchmark lexmount-webfetch` sidecar records the same version string `X.Y.Z`; custom benchmarks keep their own numbers; `bench-v*` labels from before this rule, such as the dataset's `bench-v2.1.0`, stay as historical provenance. Web Search still labels its scorer with its own catalog edition.

While the software remains `0.x`, use a minor release for new functionality, score-changing fixes or breaking changes and a patch for other compatible fixes. Describe breaking changes explicitly. Release when users need an installable, reproducible checkpoint; individual commits and report rebuilds do not need new numbers. Version changes and release tags require an explicit human release request.

Software release tags use `vMAJOR.MINOR.PATCH`. The tag, GitHub release, `pyproject.toml`, `uv.lock`, wheel and installed runtime must agree on the version. Prepare the version metadata in a feature or release PR, then tag the exact main-branch commit after it lands and integrated checks pass. A merge does not itself publish a release.

To retain a pre-migration state, use an existing release tag or record the full pre-merge commit. Create another software release only when that older code needs an independently installable release; archiving alone does not require a new version number.

`1.0.0` establishes the compatibility contract above. It can cover stable tracks while others remain explicitly experimental. For the first stable Web Fetch + Web Search release, run the integrated offline collection/scoring/report tests and browser evidence workflows, and pass the existing package and CI gates on the release commit. Publish the supported track and dataset matrix in its release notes. A successful historical report alone does not test the installable release.

## Software, benchmarks and reports

| Identity | Owner | When it changes |
| --- | --- | --- |
| Webstone software, e.g. `v0.3.0` | This repository | Execution, adapters, evidence handling or reporting software is released |
| Web Fetch benchmark edition | The Webstone version: its data lock selects the dataset commit and its code holds the scoring rules | The pinned dataset or a scoring rule changes |
| Published report build | Artifact Site or selected report host | A new report build is published |

Pinning a dataset with added or removed cases is a breaking benchmark change even when the software interface stays compatible. The [benchmark contract](benchmarks.md#benchmark-editions) defines `direct`, `rescore` and `separate` comparisons. Pin upstream code commits and external input hashes alongside readable versions. Rebuilding presentation preserves saved calls and the selected benchmark edition.

## Stable release scope

Choose a stable software version after all changes in its declared release scope have landed on main and the integrated release checks pass. Version `1.0.0` establishes the documented compatibility contract; release preparation alone does not assign that version or create a tag.

Web Fetch includes LexBench-Web-Fetch integration and independently selected WebMainBench and WCXB workflows. Search uses the separate HF Search and SimpleQA definitions with Webstone's local scorer. Publish the exact supported dataset editions and provider configurations with the release. Browser Trajectory Replay and Browser Use can remain available as experimental tracks with their own environment requirements.

Web Fetch, Web Search / SERP, Browser Trajectory Replay and Browser Use have main-branch implementations. Validate the selected tracks together on the release commit before assigning a stable version. External references, browsers, self-hosted sites and provider accounts remain separate inputs.

## Release procedure

1. Select a clean main-branch base and prepare the release in a feature or release PR. Declare supported tracks, benchmark pins and migrations. Check that pending work is not described as included.
2. Set the release version in `pyproject.toml` and refresh `uv.lock`. Runtime code reads installed package metadata automatically. Project citations stay version-neutral. Add the release's scoring-source hash to `RELEASED_SCORERS`. Run `uv lock` when package metadata changes, retaining immutable dependency pins. The metadata tests check the lock, installed package, runtime version and bilingual citations.
3. Run `make setup-test` and `make ci` on the release candidate. After it lands on main, require the integrated checks to pass on the exact commit that will be tagged. The package gate checks installation outside the checkout. Follow each track's separate release gate when publishing benchmark reports; a software tag does not certify their measurements.
4. Prepare release notes with supported tracks and suites, installation commands, compatible interfaces, required external inputs and migrations. Check that the source archive and wheel contain the declared version.
5. After release approval, publish an annotated `vMAJOR.MINOR.PATCH` tag and GitHub release for that tested main-branch commit. Never move a published tag. Upload software distribution files; keep provider outputs and private report evidence outside the software release.

Use a feature branch and PR for release preparation. Merging and publishing are separate maintainer actions. Record the exact commit in bug reports and experiment methods, including for unreleased branches.

## Released versions

Each GitHub release is the version's changelog: why it was published, the
changes since its predecessor, exact source and benchmark pins, validation,
installation and comparison implications. The release page owns these details;
this table is the index.

| Software release | Why it was published | LexBench dependencies |
| --- | --- | --- |
| [0.1.0](https://github.com/lexmount/webstone/releases/tag/v0.1.0) | Preserve the original installable Web Fetch, Replay and Browser Use checkpoint | Fetch scoring in this repository; no separate LexBench package |
| [0.2.0](https://github.com/lexmount/webstone/releases/tag/v0.2.0) | Extracted Fetch dependency and shared report-build identity | Fetch benchmark 2.0.1; no Search integration |
| [0.3.0](https://github.com/lexmount/webstone/releases/tag/v0.3.0) | Add Search/SERP and shared capture/source-validation fixes | Fetch benchmark 2.0.1; Search benchmark 1.0.0 |
| [0.4.0](https://github.com/lexmount/webstone/releases/tag/v0.4.0) | Move Web Fetch and Web Search scoring into this repository; public inputs on Hugging Face | Web Fetch dataset `3d8aa5cf`; Web Search dataset `76d9c2b6` |
| [0.5.0](https://github.com/lexmount/webstone/releases/tag/v0.5.0) | The Webstone version names the Web Fetch benchmark edition; score-changing scorer fixes; corrected references | Web Fetch dataset v2.2.0 (`c12559d2`); Web Search dataset `76d9c2b6` |

Historical 0.1.0 and 0.2.0 tags retain their original commits. A software release
can support several separately selected benchmarks. Its version never changes
saved results or an existing report's population; for Web Fetch it also names
the benchmark edition that new runs use (see
[Web Fetch benchmark edition](#web-fetch-benchmark-edition)).

## Benchmark release index

| Benchmark release | Scope and reason |
| --- | --- |
| [Fetch 1.0.0](https://github.com/lexmount/lexbench-webfetch/releases/tag/bench-v1.0.0) | Preserve the historical 430-task report contract and scorer |
| [Fetch 2.0.1](https://github.com/lexmount/lexbench-webfetch/releases/tag/bench-v2.0.1) | Expanded 550-entry catalog with complete compatibility fingerprints; separate population from 1.x |
| [Search 1.0.0](https://github.com/lexmount/lexbench-websearch/releases/tag/bench-v1.0.0) | First frozen Search catalog: scenario and factual task suites |
| [Search 1.1.0](https://github.com/lexmount/lexbench-websearch/releases/tag/bench-v1.1.0) | Correct numeric scoring and bind the catalog to its actual scorer; rescore saved cohorts before comparing |
| [WebArena Replay 1.0.1](https://huggingface.co/datasets/Lexmount/LexBench-WebArena-Replay/tree/ede6b17c21cef8a496b61e81c365934db5443919) | Correct private recording hosts in the Webstone sources and their hash bindings. Exported task questions, actions, evaluator inputs and scoring rules are unchanged, so results from the exported tasks compare directly when execution settings match. Stored attempts for tasks 280 and 282 need re-evaluation, and runs from the Git source candidates for tasks 243, 404, 464, 590, 658 and 714 reached different hosts; see the [input correction](benchmarks.md#webarena-replay-input-correction) |

The WebArena Replay Hub tags `v1.0.0` and `v1.0.1` resolve to the previous and current editions pinned in the data lock. The lock records the full commit of each edition. The manifest of 1.0.1 records a `source_revision` that was a local build commit and was never published. That manifest is immutable, so the lock notes this beside the entry; use `revision` to identify the edition.

The `bench-v*` releases above each used one tag for their tasks, reference answers,
scoring rules and evaluation code. Web Search and WebArena Replay still follow
that scheme. New Web Fetch editions use the Webstone version instead (see
[Web Fetch benchmark edition](#web-fetch-benchmark-edition)); a new Web Fetch
sidecar starts that line and does not name a `bench-v2.x` sidecar as `--previous`.

## Version source and release cadence

`pyproject.toml` is the software version source. Runtime `__version__` reads the
installed distribution metadata; do not duplicate its number in Python modules
or README files. `uv.lock` records the resolved package metadata. The software
tag must equal the package version at a formal release; commits between releases
retain their exact revision and source hashes without creating new tags.

Release after a coherent set of changes is ready for users, or when a fix needs
an installable release. Do not release for each PR, dependency synchronization,
README edit or version-label correction. A benchmark edition changes only when
its measurement contract is deliberately released. Use GitHub Releases for
published versions and their dependency snapshots, rather than a changing
current-version paragraph in the README.

## Citations and experiment identity

README BibTeX and `CITATION.cff` cite the project, so they omit `version` and
`date-released`. A paper citing a particular release may add that release's
version and permanent URL. Reports must retain the software version and commit,
benchmark edition, selected suites/task hashes and scoring-source hash. Their
execution version can differ from their report-building version. A presentation
rebuild preserves the original benchmark and measured outputs.

The software release keeps its actual dependency pins; a newer upstream edition
is not adopted just by changing a displayed version. Benchmark upgrades require
the existing integration tests and a separate decision about rescoring saved
answers or collecting new answers.
