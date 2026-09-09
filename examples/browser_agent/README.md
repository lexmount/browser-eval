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

On Apple Silicon, run the official amd64 site images in a dedicated Colima VZ
profile with Rosetta enabled and at least 140 GiB of disk. The four required
images occupy about 91 GB before container writable layers. The runner mounts
Colima's QEMU interpreter only for PHP-FPM in the two Magento images because the
legacy Alpine/musl PHP-FPM binary segfaults under Rosetta; Java, GitLab, and the
remaining official image processes stay on Rosetta. This is a runtime
compatibility shim, not a change to site data or evaluation logic.

    colima start browser-eval --vm-type vz --disk 140 --memory 8 --cpus 4 --vz-rosetta

Put `OPENAI_API_KEY`, optional `OPENAI_BASE_URL`, and `COWAGENT_REPO` in this
repository's `.env`, then run:

    ./examples/browser_agent/run.sh

To validate the pipeline with the currently available GPT-5.6 model before an
Astra-enabled project is available, keep the evidence cohort separate and run:

    BROWSER_AGENT_OUTPUT_DIR="${BROWSER_EVAL_DATA_HOME:-$HOME/.browser-eval}/browser-agent/gpt-5.6-sol" \
      ./examples/browser_agent/run.sh --model gpt-5.6-sol

The selected model is immutable within one evidence directory. GPT-5.6 and
Astra attempts must never be resumed into or reported as the same cohort.

`COWAGENT_REPO` is a trusted code-execution boundary, not a data input. The
runner refuses a dirty checkout and records its Git commit in the run config.

Every task runs once per engine. Only a failed official evaluation receives one
retry. The official benchmark score is calculated from primary attempts only;
retry recovery is reported separately and never replaces the primary score.
After the run, create the deterministic per-attempt evidence review before
building or publishing the report:

    make browser-agent-review EVIDENCE="$BROWSER_AGENT_OUTPUT_DIR"
    make browser-agent-report EVIDENCE="$BROWSER_AGENT_OUTPUT_DIR"
    make browser-agent-report-check EVIDENCE="$BROWSER_AGENT_OUTPUT_DIR"
