# Contributing

PRs are welcome for adapters, execution fixes, tests, documentation and translations. Self-authored Web Fetch task requirements are maintained in [LexBench Web Fetch](https://github.com/lexmount/lexbench-webfetch); see [benchmark boundaries](docs/benchmarks.md). Run the [offline example](README.md#quickstart), then open the guide for the track you want to change.

## Report a bug

Include the task ID, method and versions, OS, command and sanitized error. Remove keys, cookies and session data.

## Make a change

| Change | Where it belongs |
| --- | --- |
| Browser or provider adapter | Existing execution modules; see the [provider contract](docs/provider-contract.md) |
| LexBench Web Fetch task requirements | [Benchmark repository](https://github.com/lexmount/lexbench-webfetch), applied to every method |
| Scoring fix | Shared scorer, scenario regressions and a recorded correction reason |
| New browser, version or sample size | Configuration or registry; reuse the runner and renderer |
| New report instance | An external instance profile, evidence and report content; reuse its build and release commands |
| Reusable logic | `harness/`; `scripts/` stays a thin command entry point |

Use English for identifiers, comments and docstrings. Store report interface text
in language catalogs, and select it through the existing report language contract.
Keep original-language task text, source quotations and multilingual test fixtures. Keep measured counts, host facts and report-specific interpretation out
of shared renderers: read them from the selected profile and bound evidence.
Published historical reports retain their own content and provenance.

Use absolute Python imports and keep ordinary imports at the top of the module.
Keep an import local when loading an optional dependency, preparing a runtime
module, avoiding a circular import or preserving test isolation.

Test both accepted and rejected scenarios. Fix scoring rules rather than editing saved verdicts. Refactoring must preserve historical measurements and certificates.

Write documentation in English. Keep the [Chinese README](docs/README.zh-CN.md) aligned when setup or usage changes; other docs do not need duplicate translations. Preserve original task text, quotations and frozen acceptance inputs. Reports follow their track's language settings.

Benchmark changes follow [benchmark editions](docs/benchmarks.md#benchmark-editions).
Keep human-readable editions, immutable dataset/scorer hashes and the package
commit together. A minor scoring correction requires common-edition rescoring;
changed task populations or requests require a major edition.

## Update Web Fetch dependencies and methods

Update the pinned evaluator with `make bump-lexbench REV=<full merged commit>`.
It verifies upstream main ancestry and updates the lock and definition hashes.
Historical report certificates retain their original evaluator identities.

Register Web Fetch products in `harness/web_fetch/data/report-methods.json`;
executable versions and run parameters come from saved evidence. See
[method metadata](harness/web_fetch/README.md#report-method-metadata) and
[adding a method or release](harness/web_fetch/README.md#adding-a-method-or-release).

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

Development checks use Python 3.11 and Node.js 22. The base example does not need Node.js.

| Check | What it covers |
| --- | --- |
| `make lint-check` | Basic Python errors across the repository and configured Ruff rules for the declared tracks |
| `make package-check` | Source archive → wheel → base/optional installation outside the checkout |
| `make test-reference-inputs` | Strict historical-input checks after importing [approved reference copies](docs/reference-inputs.md) |
| Track release gate | Evidence, report presentation and browser checks before packaging or publishing |

`make ci` includes linting and package checks. It runs without historical pages: only tests requiring absent historical inputs are skipped. Corrupt installed inputs fail; strict reference mode also fails on missing or incomplete sets.

For report releases, open success and failure details, evidence dialogs and return links on desktop and narrow viewports in both themes. Real services need the environments listed in their track guides.

<details>
<summary>What the package gate tests</summary>

The gate builds a source archive, then a wheel from that archive. It compares installed modules and resources byte for byte and exercises TypeScript tool bridges, synthetic TEDS revisions, LangChain extraction and Crawl4AI downloads against local HTTP fixtures, including HTTP failures. Missing, stale or extra installed files fail the gate. Node tests use `node:module.stripTypeScriptTypes`.

</details>

## Keep private data out of Git

Do not commit `.env`, tokens, cookies, provider outputs, run directories, generated reports or private reviews. Keep third-party sources, versions and licenses. A task definition does not grant permission to redistribute its source page.

<details>
<summary>Maintainer reference: source exports and historical inputs</summary>

A current-source export excludes old commits that may still contain externalized material. Review development history before distributing it; deleting a current file does not remove its earlier copies.

```sh
git archive --format=tar.gz --output=/tmp/browser-eval-source.tar.gz HEAD
```

The archive excludes `.git`, untracked files and local installations. Extract it, follow README setup and run `make ci` with an empty `BROWSER_EVAL_DATA_HOME`. Historical certifier sources retained by manifests are read-only, hash-bound text inputs, not executable entry points or dependencies on old Git commits. See [third-party content](THIRD_PARTY.md) and [reference inputs](docs/reference-inputs.md).

</details>

## License

Contributions use the [Apache License 2.0](LICENSE). Contributors retain copyright in their contributions; submitting a contribution grants the applicable license and does not transfer ownership to LexMount. Preserve existing copyright notices and third-party terms described in [THIRD_PARTY](THIRD_PARTY.md).

## Software releases

Follow the [release guide](docs/releases.md) for software compatibility, version changes and release checks. Benchmark editions and report builds have separate identities. Routine review fixes and report rebuilds retain the current version; bump versions and publish tags only for an explicitly requested release.
