# Contributing

PRs are welcome for browser adapters, task definitions, tests, scoring fixes, documentation and README translations. Run the offline example in [README](README.md), then read the relevant track guide. For bug reports, include the task ID, adapter type, versions, OS, reproduction command and sanitized errors. Do not include credentials or session data.

## Language

English is the primary language for documentation, contribution guidance and automated review. Keep the English README and [Chinese README](docs/README.zh-CN.md) aligned when changing setup or usage. Additional README translations are welcome; other documentation does not need a translated copy. Preserve original task text, source quotations and frozen acceptance artifacts. Report languages follow their track's build contract.

## Implementation and evidence

Extend existing execution, scoring, aggregation and report modules. Select browsers, versions and cohort sizes through configuration or registries rather than copying runners or renderers. Commands belong in `scripts/`; reusable logic belongs in `harness/`.

Behavior changes need scenario tests for both accepted and rejected inputs. Scoring corrections also need regression controls through the shared scorer and revision provenance. Do not patch stored verdicts. Refactoring must not re-certify historical measurements or certificates.

## Checks

Run `make ci` before submitting. Development tests use Node.js 22, including `node:module.stripTypeScriptTypes`; the base Python example does not need Node.js. `make lint-check` checks basic Python errors across the repository and applies the full configured Ruff rules to the declared tracks. Both are part of CI.

`make ci` includes `make package-check`: build a source archive, build a wheel from it, then test base and optional installations outside the checkout. The gate checks current modules and declared resources byte for byte, TypeScript tool bridging, synthetic TEDS revisions, LangChain extraction and Crawl4AI downloads against local HTTP fixtures. It rejects missing, stale or extra installed files. Use `make package-check` separately when changing distribution configuration.

For report releases, also run the track's full release gate. Inspect success and failure details, evidence dialogs and return navigation at desktop and narrow widths in both themes. Real-service requirements are documented in the track guides.

Tests do not download historical pages. Install approved external inputs using the [reference-input guide](docs/reference-inputs.md), then run `make test-reference-inputs` for historical checks. Strict mode rejects missing, changed or incomplete inputs before tests start. Ordinary mode skips only checks requiring absent historical inputs; corrupt installed inputs still fail.

## Distribution boundaries

Do not commit `.env`, tokens, cookies, provider outputs, run directories, generated reports or private review records. Retain third-party origins, versions and licenses. Adding a task definition does not grant redistribution rights to its source page.

For the first public release, export the reviewed current tree and initialize a new public history. Do not push development history that still contains externalized source material; deleting a current file does not remove earlier copies.

```sh
git archive --format=tar.gz --output=/tmp/browser-eval-source.tar.gz HEAD
```

The archive excludes `.git`, untracked files and local installations. Extract it, follow README setup and run `make ci` with an empty `BROWSER_EVAL_DATA_HOME`. Historical certifier sources retained by manifests are read-only, hash-bound text inputs, not executable entry points or dependencies on old Git commits. See [third-party content](THIRD_PARTY.md) and [reference inputs](docs/reference-inputs.md).

Optional Claude review requires maintainers to set `ENABLE_CLAUDE_REVIEW=true` and `CLAUDE_CODE_OAUTH_TOKEN`. Ordinary CI needs neither. Review runs on disposable GitHub-hosted runners without personal machine paths.
