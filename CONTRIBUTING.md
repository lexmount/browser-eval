# Contributing

PRs are welcome for adapters, tasks, tests, scoring fixes, documentation and translations. Run the [offline example](README.md#quickstart), then open the guide for the track you want to change.

## Report a bug

Include the task ID, method and versions, OS, command and sanitized error. Remove keys, cookies and session data.

## Make a change

| Change | Where it belongs |
| --- | --- |
| Browser or provider adapter | Existing execution modules; see the [provider contract](docs/provider-contract.md) |
| Task requirements | Dataset configuration, applied to every method |
| Scoring fix | Shared scorer, scenario regressions and a recorded correction reason |
| New browser, version or sample size | Configuration or registry; reuse the runner and renderer |
| Reusable logic | `harness/`; `scripts/` stays a thin command entry point |

Test both accepted and rejected scenarios. Fix scoring rules rather than editing saved verdicts. Refactoring must preserve historical measurements and certificates.

Write documentation in English. Keep the [Chinese README](docs/README.zh-CN.md) aligned when setup or usage changes; other docs do not need duplicate translations. Preserve original task text, quotations and frozen acceptance inputs. Reports follow their track's language settings.

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

Optional Claude review requires `ENABLE_CLAUDE_REVIEW=true` and `CLAUDE_CODE_OAUTH_TOKEN`. Ordinary CI needs neither. Review uses disposable GitHub-hosted runners.
