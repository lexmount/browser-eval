# Contributing

Start with the [offline example](README.md#quickstart). Keep changes small and reuse the existing runner, scorer and renderer.

## Report a bug

Search existing issues, then use the [bug](.github/ISSUE_TEMPLATE/bug_report.md), [feature](.github/ISSUE_TEMPLATE/feature_request.md) or [evaluation task](.github/ISSUE_TEMPLATE/evaluation_task.md) template. Follow the destination repository's template for upstream reports.

- Show a minimal command/input, concrete expected output, actual response or error, and the pass/fail condition.
- Include versions, environment and observation date; hosted calls also need the mode and request ID. Distinguish current observations from saved evidence and confirmed causes from hypotheses.
- State the task's required content. Extra baseline output is not an acceptance requirement. Keep the key diagram visible; link or fold long evidence. Remove credentials and session data.
- Group evaluation findings under a report-linked Task with native sub-issues, failure counts and denominators. Separate deferred capabilities. Close items only after verifying the original failure.

## Make a change

| Change | Owner |
| --- | --- |
| Provider or browser behavior | Existing execution modules; [provider contract](docs/provider-contract.md) |
| Public tasks and references | [Hugging Face datasets](https://huggingface.co/Lexmount); [benchmark boundaries](docs/benchmarks.md) |
| Scoring | Shared scorer and scenario regressions; never patch saved verdicts |
| New method or report instance | Registry/configuration and external evidence/profile; reuse the pipeline |
| Reusable logic | `src/harness/`; command scripts stay thin |

Test accepted and rejected scenarios. Preserve historical measurements and certificates. Keep instance-specific counts, hosts and prose in bound evidence, outside shared renderers.

Use absolute imports at module level. Local imports are appropriate for optional dependencies, runtime setup, cycles or test isolation. Preserve hash-bound scorer imports until their authorized revision. Text I/O names `encoding="utf-8"`.

Keep the [Chinese README](docs/README.zh-CN.md) aligned with setup and usage changes. Other guides need no duplicate translation. Reports use their [language contract](docs/report-hosting.md#report-languages).

### Text language

Project-authored identifiers, comments, docs, logs, errors and report interfaces use English as their source. Chinese report text comes from language catalogs.

External queries, tasks, references, responses and quotations keep their original language and bytes. The same applies to fixtures representing external text and hash-bound acceptance/audit inputs; language checks must exempt them. See [reference inputs](docs/reference-inputs.md).

## Update Web Fetch dependencies and methods

Pin datasets with `make pin-dataset DATASET=<track> REV=<full-HF-commit>`. It verifies main-history membership, archive and file hashes, then updates the lock and import bindings. Historical certificates retain their evaluator identities; see [benchmark editions](docs/benchmarks.md#benchmark-editions).

Register products in `src/harness/web_fetch/data/report-methods.json`; versions and run parameters come from evidence. See [method metadata](src/harness/web_fetch/README.md#report-method-metadata) and [adding a method](src/harness/web_fetch/README.md#adding-a-method-or-release).

## PR and commit titles

Use `<type>(<scope>): <summary>`; scope is optional. Types: `fix`, `feat`, `docs`, `refactor`, `test`, `perf`, `build`, `ci`, `chore`.

Write an English imperative describing the complete change, preferably within 72 characters and without a final period. Mark breaking changes with `!` and explain migration. Keep the PR title aligned with its final diff; do not rewrite shared history solely to rename commits.

Examples: `fix(scoring): require facts in the returned body`; `docs: shorten contribution guidance`.

## Run the checks

Use Python from `.python-version` and Node.js 22. Full checks also need [ShellCheck](https://github.com/koalaman/shellcheck#installing) and Playwright Chromium.

```bash
make setup-test
make ci
```

While editing, run the relevant checks:

```bash
make docs-check
make test-python TEST_PATHS=tests/web_search
make lint-check
make browser-check
make package-check
```

`TEST_PATHS` selects tests; `PYTEST_ARGS` adds pytest options, such as `-n 4 --dist worksteal`. `make help` lists targets. Format selected files with `make format FILES="path.py ..."`.

CI checks the complete PR. Only README, contribution/security guidance, `AGENTS.md`, Markdown under `docs/`, and images under `docs/assets/` qualify for documentation-only checks. Other paths, unknown scope, main pushes and tags run full checks. The required `Python 3.11` status rejects failed, cancelled or unexpectedly skipped jobs. See [workflow details](.github/workflows/README.md).

Report output changes must update the golden snapshots deliberately:

```bash
UPDATE_GOLDEN=1 uv run --frozen --all-extras python -m pytest tests/test_golden_reports.py
```

For report delivery, use the track's [release gates](.agents/skills/benchmark-report/references/report-and-release.md#release-gates). Historical-input checks follow [reference inputs](docs/reference-inputs.md).

## Keep private data out of Git

Keep credentials, provider outputs, run directories, generated reports and private reviews outside Git. Preserve source attribution and redistribution rights; see [third-party content](THIRD_PARTY.md). A source archive excludes Git history, but deleting a file does not erase earlier commits.

## License

Contributions use [Apache License 2.0](LICENSE); contributors retain copyright. Preserve existing notices and third-party terms in [THIRD_PARTY.md](THIRD_PARTY.md).

## Software releases

Follow the [release guide](docs/releases.md). Version changes require an explicit human release request; code synchronization and report rebuilds do not authorize a bump or tag.
