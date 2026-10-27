# Software releases

Browser Eval versions the execution and reporting software. The current package version is `0.3.0`; a version in source is not a published GitHub release. Software tags use `vMAJOR.MINOR.PATCH` and identify one tested commit.

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
| Benchmark edition, e.g. `bench-v2.0.1` | Selected benchmark repository | Tasks, references or scoring change under that benchmark's rules |
| Published report build | Artifact Site or selected report host | A new report build is published |

Added cases can require a major benchmark edition even when accepting that edition is a compatible software feature. The [benchmark contract](benchmarks.md#benchmark-editions) defines `direct`, `rescore` and `separate` comparisons. Pin upstream code commits and external input hashes alongside readable versions. Rebuilding presentation preserves saved calls and the selected benchmark edition.

## Stable release scope

Choose a stable software version after all changes in its declared release scope have landed on main and the integrated release checks pass. Version `1.0.0` establishes the documented compatibility contract; release preparation alone does not assign that version or create a tag.

Web Fetch includes LexBench Web Fetch integration and independently selected WebMainBench and WCXB workflows. Search uses LexBench Web Search definitions and scoring. Publish the exact supported dataset editions and provider configurations with the release. Browser Trajectory Replay and Browser Use can remain available as experimental tracks with their own environment requirements.

Web Fetch, Web Search / SERP, Browser Trajectory Replay and Browser Use have main-branch implementations. Validate the selected tracks together on the release commit before assigning a stable version. External references, browsers, self-hosted sites and provider accounts remain separate inputs.

## Release procedure

1. Select a clean commit on main and declare supported tracks, benchmark pins and migrations. Check that pending work is not described as included.
2. Align `pyproject.toml`, `harness/web_fetch/__init__.py` and `uv.lock`. Project citations stay version-neutral. Run `uv lock` when package metadata changes, retaining immutable dependency pins. The metadata tests check the lock, installed package, runtime version and bilingual citations.
3. Run `make setup-test` and `make ci` on that commit. The package gate checks installation outside the checkout. Follow each track's separate release gate when publishing benchmark reports; a software tag does not certify their measurements.
4. Prepare release notes with supported tracks and suites, installation commands, compatible interfaces, required external inputs and migrations. Check that the source archive and wheel contain the declared version.
5. After release approval, publish an annotated `vMAJOR.MINOR.PATCH` tag and GitHub release for that exact commit. Never move a published tag. Upload software distribution files; keep provider outputs and private report evidence outside the software release.

Use a feature branch and PR for release preparation. Merging and publishing are separate maintainer actions. Record the exact commit in bug reports and experiment methods, including for unreleased branches.

## Release history and the next checkpoint

| Version | Scope | Source |
| --- | --- | --- |
| `0.1.0` | Original software checkpoint | `03c9a5e94619000480c92b8e517c16165dc2a7f4` |
| `0.2.0` | Shared report-build identity and reusable Web Fetch reports | `30645af1ba2520b4d089cbef32979e088cffe277` |
| `0.3.0` | Web Search integration, shared capture fixes and release metadata checks | This release preparation; tag the merged commit after validation |

The first two versions were previously tagged. Restore deleted tags to their
original Git objects; never reuse their names for newer code. Restoration does
not rerun an experiment or alter its results. Use `0.3.1` for a compatible fix
after this release, `0.4.0` for another feature release or a breaking change
while developing, and `1.0.0` when the documented interfaces are ready for a
long-term compatibility promise. A stable Web Fetch workflow alone does not
make every track a stable API.

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
