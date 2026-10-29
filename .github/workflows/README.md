# GitHub workflows

`ci.yml` tests main pushes, release tags and pull requests. CI uses Python 3.11 and uv 0.7.8, with Node.js 22 for Node checks. Contributors use `make setup-test` and `make ci` locally.

Three jobs run independently:

| Job | Coverage |
| --- | --- |
| Tests and static checks | `make ci-tests`: review guidance, naming, lint, the complete Python suite, Node UI/native-adapter tests and the fixture smoke test |
| Installation and packaging | Base installation and CLI language checks, sdist/pip installation, wheel smoke checks and all existing optional installation profiles |
| Rendered reference and report checks | Chromium reference capture, report interactions and Web Search visual checks |

The Python suite uses the worker count configured in `ci.yml`, with `--dist loadfile` keeping each test file in one process. Slow-test durations appear in the job log. Local `make ci` remains serial by default; use `make ci PYTEST_ARGS='-n 4 --dist loadfile --durations=20'` for parallel test scheduling.

`make review-check`, part of both `make ci-tests` and local `make ci`, verifies the pinned shared review guide and synchronization tool against `review-guidelines.lock.json`, and confirms that OCR loads the guide alongside its built-in rules. This offline integrity check needs no cross-repository credentials. Use `uv run --frozen python scripts/review_guidelines.py check --source <ai-template-py-checkout>` to also compare the files with their recorded source commit. Shared requirements are maintained in `lexmount/ai-template-py`; Browser Eval's review supplement is the section after the repository supplement marker at the end of `docs/references/review-guidelines.md`; the lock covers only the text before it, and synchronization preserves it.

The existing `Python 3.11` status succeeds only when all three jobs succeed. A failed, cancelled or skipped job fails that status. New commits cancel obsolete CI runs for the same pull request. Main and tag runs each have a unique concurrency group, so a newer run cannot replace an earlier pending run.

Job timeouts include setup: 20 minutes for tests, 15 for packaging, 10 for browser checks and 3 for the aggregate status. The browser test step retains its five-minute limit. Action references are pinned to full commits; Python, Node.js and uv versions are set through action inputs.

Code review is dispatched by the organization's [public OCR workflow](https://github.com/lexmount/github-policy-public/blob/main/.github/workflows/codex-code-review.yml). The dispatch job confirms durable task acceptance; the separate `ocr-review` status reports the backend's findings. The repository does not need a copied OCR workflow or local review credentials. Organization Rulesets manage review requirements separately from CI.

Project requirements remain in [AGENTS.md](../../AGENTS.md), with benchmark-specific requirements in [.agents/skills/benchmark-report](../../.agents/skills/benchmark-report/SKILL.md). Backend review-policy configuration is managed separately from the repository's workflows.
