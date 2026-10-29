# Software releases

Browser Eval versions the execution and reporting software. Software tags use `vMAJOR.MINOR.PATCH` and identify one tested commit.

## Version rules

Follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The public compatibility contract covers documented CLI commands and options, dataset/provider inputs, and reading saved evidence through supported report builders. Internal Python helpers and generated HTML markup are not public APIs.

| Change | Version change | Example after 1.0.0 |
| --- | --- | --- |
| Compatible bug fix or a documentation-only release | Patch | `1.0.0` → `1.0.1` |
| Compatible new adapter, command, track or optional input field | Minor | `1.0.1` → `1.1.0` |
| Remove or rename a public command, require incompatible inputs, or stop reading supported evidence without migration | Major | `1.1.0` → `2.0.0` |

While the software remains `0.x`, use a minor release for new functionality or breaking changes and a patch for compatible fixes. Describe breaking changes explicitly. Release when users need an installable, reproducible checkpoint; individual commits and report rebuilds do not need new numbers. Version changes and release tags require an explicit human release request.

`1.0.0` establishes the compatibility contract above. It can cover stable tracks while others remain explicitly experimental. For the first stable Web Fetch + Web Search release, run the integrated offline collection/scoring/report tests and browser evidence workflows, and pass the existing package and CI gates on the release commit. Publish the supported track and dataset matrix in its release notes. A successful historical report alone does not test the installable release.

## Software, benchmarks and reports

| Identity | Owner | When it changes |
| --- | --- | --- |
| Browser Eval software, e.g. `v0.3.0` | This repository | Execution, adapters, evidence handling or reporting software is released |
| Benchmark edition | Selected HF dataset commit plus the Browser Eval scorer revision | Tasks, references or scoring change under the benchmark's compatibility rules |
| Published report build | Artifact Site or selected report host | A new report build is published |

Added cases can require a major benchmark edition even when accepting that edition is a compatible software feature. The [benchmark contract](benchmarks.md#benchmark-editions) defines `direct`, `rescore` and `separate` comparisons. Pin upstream code commits and external input hashes alongside readable versions. Rebuilding presentation preserves saved calls and the selected benchmark edition.

## Stable release scope

Choose a stable software version after all changes in its declared release scope have landed on main and the integrated release checks pass. Version `1.0.0` establishes the documented compatibility contract; release preparation alone does not assign that version or create a tag.

Web Fetch includes LexBench Web Fetch integration and independently selected WebMainBench and WCXB workflows. Search uses the separate HF Search and SimpleQA definitions with Browser Eval's local scorer. Publish the exact supported dataset editions and provider configurations with the release. Browser Trajectory Replay and Browser Use can remain available as experimental tracks with their own environment requirements.

Web Fetch, Web Search / SERP, Browser Trajectory Replay and Browser Use have main-branch implementations. Validate the selected tracks together on the release commit before assigning a stable version. External references, browsers, self-hosted sites and provider accounts remain separate inputs.

## Release procedure

1. Select a clean commit on main and declare supported tracks, benchmark pins and migrations. Check that pending work is not described as included.
2. Set the release version in `pyproject.toml` and refresh `uv.lock`. Runtime code reads installed package metadata automatically. Project citations stay version-neutral. Run `uv lock` when package metadata changes, retaining immutable dependency pins. The metadata tests check the lock, installed package, runtime version and bilingual citations.
3. Run `make setup-test` and `make ci` on that commit. The package gate checks installation outside the checkout. Follow each track's separate release gate when publishing benchmark reports; a software tag does not certify their measurements.
4. Prepare release notes with supported tracks and suites, installation commands, compatible interfaces, required external inputs and migrations. Check that the source archive and wheel contain the declared version.
5. After release approval, publish an annotated `vMAJOR.MINOR.PATCH` tag and GitHub release for that exact commit. Never move a published tag. Upload software distribution files; keep provider outputs and private report evidence outside the software release.

Use a feature branch and PR for release preparation. Merging and publishing are separate maintainer actions. Record the exact commit in bug reports and experiment methods, including for unreleased branches.

## Released versions

Each GitHub release is the version's changelog: why it was published, the
changes since its predecessor, exact source and benchmark pins, validation,
installation and comparison implications. The release page owns these details;
this table is the index.

| Software release | Why it was published | LexBench dependencies |
| --- | --- | --- |
| [0.1.0](https://github.com/lexmount/browser-eval/releases/tag/v0.1.0) | Preserve the original installable Web Fetch, Replay and Browser Use checkpoint | Fetch scoring in this repository; no separate LexBench package |
| [0.2.0](https://github.com/lexmount/browser-eval/releases/tag/v0.2.0) | Extracted Fetch dependency and shared report-build identity | Fetch benchmark 2.0.1; no Search integration |
| [0.3.0](https://github.com/lexmount/browser-eval/releases/tag/v0.3.0) | Add Search/SERP and shared capture/source-validation fixes | Fetch benchmark 2.0.1; Search benchmark 1.0.0 |

Historical 0.1.0 and 0.2.0 tags retain their original commits. A software release
can support several separately selected benchmarks; its version never chooses
a report's population or changes saved results.

## Benchmark release index

| Benchmark release | Scope and reason |
| --- | --- |
| [Fetch 1.0.0](https://github.com/lexmount/lexbench-webfetch/releases/tag/bench-v1.0.0) | Preserve the historical 430-task report contract and scorer |
| [Fetch 2.0.1](https://github.com/lexmount/lexbench-webfetch/releases/tag/bench-v2.0.1) | Expanded 550-entry catalog with complete compatibility fingerprints; separate population from 1.x |
| [Search 1.0.0](https://github.com/lexmount/lexbench-websearch/releases/tag/bench-v1.0.0) | First frozen Search catalog: scenario and factual task suites |
| [Search 1.1.0](https://github.com/lexmount/lexbench-websearch/releases/tag/bench-v1.1.0) | Correct numeric scoring and bind the catalog to its actual scorer; rescore saved cohorts before comparing |

A benchmark release uses one tag for its tasks, reference answers, scoring rules
and evaluation code. Task suites use stable names rather than separate dataset
release numbers. Data unchanged with updated tooling can still warrant a bench
release: use a patch when measurement is unchanged, a minor for scoring
corrections, and a major for changed task scope. Python requires package version metadata for installation; this is
internal packaging information, not a separate public release line.

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
