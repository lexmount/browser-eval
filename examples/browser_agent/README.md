# Autonomous Browser-Agent Evaluation

This track runs the real CowAgent decision loop against the official
WebArena-Verified evaluator. It is independent from `harness/browser_kernel`,
which replays a fixed list of browser-tool calls to isolate browser-engine
behavior and does not measure autonomous planning.

The pilot selection is pre-registered in
`evaluation/browser-agent/webarena-verified-pilot.json`: 5 easy, 10 medium, and
15 hard cases. These are project-defined operational tiers based on an audited
minimum number of visible UI operations, not WebArena's official hard-subset
label. The pilot uses the self-hosted shopping, shopping_admin, reddit, and
gitlab sites. Wikipedia and Map are deferred because their official local setup
requires large external data bundles.

Put `OPENAI_API_KEY`, optional `OPENAI_BASE_URL`, and `COWAGENT_REPO` in this
repository's `.env`, then run:

    ./examples/browser_agent/run.sh

`COWAGENT_REPO` is a trusted code-execution boundary, not a data input. The
runner refuses a dirty checkout and records its Git commit in the run config.

Every task runs once per engine. Only a failed official evaluation receives one
retry. The official benchmark score is calculated from primary attempts only;
retry recovery is reported separately and never replaces the primary score.
