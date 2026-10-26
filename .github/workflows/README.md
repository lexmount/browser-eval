# GitHub workflows

`ci.yml` tests main pushes and pull requests. The base CLI runs on Python 3.12–3.14; full development checks use Python 3.11, uv 0.7.8 and Node.js 22. Contributors use `make setup-test` and `make ci` locally.

Code review is dispatched by the organization's [public OCR workflow](https://github.com/lexmount/github-policy-public/blob/main/.github/workflows/codex-code-review.yml). The dispatch job confirms durable task acceptance; the separate `ocr-review` status reports the backend's findings. The repository does not need a copied OCR workflow or local review credentials. Organization Rulesets manage review requirements separately from CI.

Project requirements remain in [AGENTS.md](../../AGENTS.md), with benchmark-specific requirements in [.agents/skills/benchmark-report](../../.agents/skills/benchmark-report/SKILL.md). Backend review-policy configuration is managed separately from the repository's workflows.
