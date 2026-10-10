# GitHub workflows

`ci.yml` runs on main pushes and pull requests. Disposable GitHub-hosted runners install Python 3.11, uv 0.7.8 and Node.js 22, then run `make setup-test` and `make ci`. Contributors use the same local entry points.

`claude.yml` provides optional English code review. Maintainers must set `ENABLE_CLAUDE_REVIEW=true` and `CLAUDE_CODE_OAUTH_TOKEN`; ordinary CI does not require them. Review uses GitHub-hosted runners, without a personal host or local Claude/Bun installation.

When enabled, opened, updated, ready-for-review and reopened non-draft PRs trigger review. `@claude` in issues, PR comments or reviews uses the interactive step and receives the comment's request. `CLAUDE_REVIEW_STANDARDS` owns shared review guidance and references `AGENTS.md` and relevant skills. New commits cancel older review runs for the same PR.
