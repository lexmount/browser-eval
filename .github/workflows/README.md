# GitHub workflows

`ci.yml` tests main pushes, release tags and pull requests. CI uses Python 3.11 and uv 0.7.8, with Node.js 22 for Node checks. Contributors use `make setup-test` and `make ci` locally.

Three jobs run independently:

| Job | Coverage |
| --- | --- |
| Tests and static checks | `make ci-tests`: review guidance, naming, lint, the complete Python suite, Node UI/native-adapter tests and the fixture smoke test |
| Installation and packaging | Base installation and CLI language checks, sdist/pip installation, wheel smoke checks and all existing optional installation profiles |
| Rendered reference and report checks | Chromium reference capture, report interactions and Web Search visual checks |

The Python suite uses two workers with `--dist loadfile`, keeping each test file in one process. Slow-test durations appear in the job log. Local `make ci` remains serial by default; use `make ci PYTEST_ARGS='-n 2 --dist loadfile --durations=20'` for the same test scheduling.

`make review-check` runs inside both `make ci-tests` and local `make ci`. The version 2 `review-guidelines.lock.json` pins only the shared policy text in `docs/references/review-guidelines.md`, using a full merged commit from `lexmount/ai-template-py` and a SHA-256 checksum. Maintenance logic in `harness/review_guidelines.py` is ordinary repository code, covered by the default Python test suite; `scripts/review_guidelines.py` is its command entry point.

OCR reads `.opencodereview/review-rules.generated.md`, containing the complete shared guide and Browser Eval's supplement from `.opencodereview/review-rules.md`, alongside its built-in rules. The supplement explicitly points to `AGENTS.md` and `CONTRIBUTING.md`. OCR selects the first matching rule, so adding another rule does not combine their contents. Verification checks each reachable route and rejects a route that drops shared guidance, local requirements or built-in rules.

After editing the local supplement, regenerate the review entry and check it:

```sh
uv run --frozen python scripts/review_guidelines.py render
make review-check
```

To update shared policy, use a trusted template checkout containing the selected merged commit:

```sh
uv run --frozen python scripts/review_guidelines.py sync --source <ai-template-py-checkout> --ref <full-merged-commit> .
uv run --frozen python scripts/review_guidelines.py check --source <ai-template-py-checkout>
```

Synchronization updates the shared guide, generated entry and source lock; it preserves local requirements and executable code. Targets must already configure the generated review entry. Standard HTTPS and SSH remote spellings are accepted under any remote name. The configured URL identifies the intended source; use a trusted checkout because offline checks do not authenticate GitHub. The ordinary check verifies local checksums and composition without network access; `--source` additionally compares bytes with the recorded Git commit.

Batch updates validate all destinations first, then process repositories sequentially. Complete files are staged on the destination filesystem and replaced atomically, with the lock last. A replacement failure can leave mixed versions in the current repository, which verification rejects; retrying the same source revision repairs it. Earlier completed repositories retain their updates. Synchronization does not commit or push changes.

The existing `Python 3.11` status succeeds only when all three jobs succeed. A failed, cancelled or skipped job fails that status. New commits cancel obsolete CI runs for the same pull request. Main and tag runs each have a unique concurrency group, so a newer run cannot replace an earlier pending run.

Job timeouts include setup: 20 minutes for tests, 15 for packaging, 10 for browser checks and 3 for the aggregate status. The browser test step retains its five-minute limit. Action references are pinned to full commits; Python, Node.js and uv versions are set through action inputs.

Code review is dispatched by the organization's [public OCR workflow](https://github.com/lexmount/github-policy-public/blob/main/.github/workflows/codex-code-review.yml). The dispatch job confirms durable task acceptance; the separate `ocr-review` status reports the backend's findings. The repository does not need a copied OCR workflow or local review credentials. Organization Rulesets manage review requirements separately from CI.

Project requirements remain in [AGENTS.md](../../AGENTS.md), with benchmark-specific requirements in [.agents/skills/benchmark-report](../../.agents/skills/benchmark-report/SKILL.md). Backend review-policy configuration is managed separately from the repository's workflows.
