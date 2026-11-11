# GitHub workflows

`ci.yml` tests main pushes, release tags and pull requests. CI uses Python 3.11 and uv 0.7.8, with Node.js 22 for Node checks. Contributors use `make setup-test` and `make ci` locally.

`Select check scope` compares the complete PR merge tree with its base.
Documentation-only PRs use `make docs-check`; installation and browser jobs are
skipped. The [contributor guide](../../CONTRIBUTING.md#run-the-checks) lists the
eligible paths. Unknown paths or missing history require full checks, as do all
main pushes and tags. Full checks run two Python partitions, installation and
browser checks in parallel. Browser checks use Ubuntu 24.04, supported by the locked
Playwright; `ubuntu-latest` can advance to an unsupported browser platform:

| Job | Coverage |
| --- | --- |
| Tests and static checks (primary) | First Python shard, naming, lint, Node UI/native-adapter tests and the fixture smoke test |
| Tests and static checks (secondary) | Second Python shard; no duplicate static or Node checks |
| Installation and packaging | Base installation and CLI language checks, sdist/pip installation, wheel smoke checks and all existing optional installation profiles |
| Rendered reference and report checks | Chromium reference capture, report interactions and Web Search visual checks |

Each Python test's node ID selects one of two shards through SHA-256, independent
of Python's random hash seed. Both shards use the full inventory, so slow tests and
new directories can reach either runner. The three browser-test modules listed in
the Makefile run only in the dedicated browser job. Together these jobs cover the
complete Python suite without overlap. All must pass the existing required status.
Documentation-only PRs run only the primary partition's documentation checks.
Each Python partition uses the worker count configured in `ci.yml`, with
`--dist worksteal` redistributing pending tests when a worker becomes available.
Slow-test durations appear in the job log. Local `make ci` remains serial by
default; use `make ci PYTEST_ARGS='-n 4 --dist worksteal --durations=20'` for
parallel scheduling. `TEST_PATHS` selects files or directories for focused local
Python checks.

OCR loads `docs/references/review-guidelines.md`: the shared guide copied from `lexmount/ai-template-py`, with its source commit in the first line, followed by Webstone's supplement. To update the shared section, copy the new upstream text in an ordinary PR and update the commit.

The existing `Python 3.11` status requires successful scope selection and tests.
Full checks require both test partitions and successful installation and browser jobs; the
documentation path requires those two jobs to be skipped. Failures, cancellations,
unexpected skips and unknown scopes fail the status. New commits cancel obsolete
CI runs for the same pull request. Main and tag runs each have a unique concurrency
group, so a newer run cannot replace an earlier pending run.

Job timeouts include setup: 20 minutes for tests, 15 for packaging, 10 for browser checks and 3 each for scope selection and the aggregate status. The browser test step retains its five-minute limit. Action references are pinned to full commits; Python, Node.js and uv versions are set through action inputs.

Code review is dispatched by the organization's [public OCR workflow](https://github.com/lexmount/github-policy-public/blob/main/.github/workflows/codex-code-review.yml). The dispatch job confirms durable task acceptance; the separate `ocr-review` status reports the backend's findings. The repository does not need a copied OCR workflow or local review credentials. Organization Rulesets manage review requirements separately from CI.

Project requirements remain in [AGENTS.md](../../AGENTS.md), with benchmark-specific requirements in [.agents/skills/benchmark-report](../../.agents/skills/benchmark-report/SKILL.md). Backend review-policy configuration is managed separately from the repository's workflows.
