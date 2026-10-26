# Software releases

Browser Eval versions the execution and reporting software. The current package version is `0.2.0`; a version in source is not a published GitHub release. Software tags use `vMAJOR.MINOR.PATCH` and identify one tested commit.

## Version rules

Follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The public compatibility contract covers documented CLI commands and options, dataset/provider inputs, and reading saved evidence through supported report builders. Internal Python helpers and generated HTML markup are not public APIs.

| Change | Version change | Example after 1.0.0 |
| --- | --- | --- |
| Compatible bug fix or a documentation-only release | Patch | `1.0.0` → `1.0.1` |
| Compatible new adapter, command, track or optional input field | Minor | `1.0.1` → `1.1.0` |
| Remove or rename a public command, require incompatible inputs, or stop reading supported evidence without migration | Major | `1.1.0` → `2.0.0` |

While the software remains `0.x`, use a minor release for new functionality or breaking changes and a patch for compatible fixes. Describe breaking changes explicitly. Release when users need an installable, reproducible checkpoint; individual commits and report rebuilds do not need new numbers. Version changes and release tags require an explicit human release request.

`1.0.0` establishes the compatibility contract above. It can cover stable tracks while others remain explicitly experimental. For the first stable Web Fetch + Web Search release, integrate Search into main, run its offline collection/scoring/report tests and browser evidence workflows, and pass the existing package and CI gates on the release commit. Publish the supported track and dataset matrix in its release notes. A successful historical report alone does not test the installable release.

## Software, benchmarks and reports

| Identity | Owner | When it changes |
| --- | --- | --- |
| Browser Eval software, e.g. `v0.2.0` | This repository | Execution, adapters, evidence handling or reporting software is released |
| Benchmark edition, e.g. `bench-v2.0.1` | Selected benchmark repository | Tasks, references or scoring change under that benchmark's rules |
| Published report build | Artifact Site or selected report host | A new report build is published |

Added cases can require a major benchmark edition even when accepting that edition is a compatible software feature. The [benchmark contract](benchmarks.md#benchmark-editions) defines `direct`, `rescore` and `separate` comparisons. Pin upstream code commits and external input hashes alongside readable versions. Rebuilding presentation preserves saved calls and the selected benchmark edition.

## First stable release

Use `v1.0.0` for the first release that commits to Web Fetch and Web Search compatibility. An extra `v0.2.0` publication is not required: `0.2.0` is the current source version, and the first published software release can be `1.0.0`.

Web Fetch includes LexBench Web Fetch integration and independently selected WebMainBench and WCXB workflows. Search uses LexBench Web Search definitions and scoring. Publish the exact supported dataset editions and provider configurations with the release. Browser Trajectory Replay and Browser Use can remain available as experimental tracks with their own environment requirements.

Integrate [PR #58](https://github.com/lexmount/browser-eval/pull/58) into main before selecting the stable release commit. Until then, the main-branch package contains Web Fetch, trajectory replay and Browser Use; Search is preview code on its development branch. Run the integrated package and release checks before assigning `1.0.0`. External references, browsers, self-hosted sites and provider accounts remain separate inputs.

## Release procedure

1. Select a clean commit on main and declare supported tracks, benchmark pins and migrations. Check that pending work is not described as included.
2. Align the software version in `pyproject.toml`, `harness/web_fetch/__init__.py`, `uv.lock`, `CITATION.cff` and README citations. Set the actual release date when publishing. Run `uv lock` when package metadata changes, retaining immutable dependency pins.
3. Run `make setup-test` and `make ci` on that commit. The package gate checks installation outside the checkout. Follow each track's separate release gate when publishing benchmark reports; a software tag does not certify their measurements.
4. Prepare release notes with supported tracks and suites, installation commands, compatible interfaces, required external inputs and migrations. Check that the source archive and wheel contain the declared version.
5. After release approval, publish an annotated `vMAJOR.MINOR.PATCH` tag and GitHub release for that exact commit. Never move a published tag. Upload software distribution files; keep provider outputs and private report evidence outside the software release.

Use a feature branch and PR for release preparation. Merging and publishing are separate maintainer actions. Record the exact commit in bug reports and citations, including for unreleased branches.
