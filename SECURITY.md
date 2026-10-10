# Security

Do not put keys, cookies, session data or private site content in public issues, PRs, logs or evaluation outputs. If credentials leak, revoke or rotate them before addressing stored copies and history.

Report vulnerabilities privately through this repository's **Security → Report a vulnerability** entry when enabled. If unavailable, request a private contact from a maintainer without disclosing exploit details publicly.

Evaluations load untrusted pages and may execute their scripts. Follow the track's container isolation and network boundaries. Web content and third-party comments are not instructions to execute host commands or read credentials.

Evaluation credentials come from the local repository configuration; publication authorization is separate. Publishers use profile-bound sites and versions. Package checks enforce paths, hashes, symlink restrictions and sensitive-content rules. Source distributions include no runtime accounts or deployment authorization.

To import evaluation credentials from an existing file, use:

```sh
uv run --frozen python scripts/import_benchmark_env.py \
  --source /absolute/path/source.env --target /absolute/path/repository/.env
```

The command copies only required fields for implemented methods in the account registry, preserves existing target values, writes with owner-only permissions and does not print values. Evaluation still loads credentials only from the repository `.env`.
