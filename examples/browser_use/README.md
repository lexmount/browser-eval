# Run Browser Use

CowAgent makes the decisions; WebArena-Verified checks task completion. Use this track to compare browsers with the same Agent, model and tools. For fixed actions without Agent decisions, use [trajectory replay](../trajectory_replay/README.md).

## Prepare the environment

| Needed | Where to configure it |
| --- | --- |
| Clean CowAgent checkout | `COWAGENT_REPO` in the repository `.env`; the runner records its commit and refuses uncommitted changes |
| Model account | `OPENAI_API_KEY`, optional `OPENAI_BASE_URL`, in `.env` |
| WebArena sites | Shopping, Shopping Admin, Reddit and GitLab |
| WebArena site accounts | `WEBARENA_<SITE>_USERNAME` and `WEBARENA_<SITE>_PASSWORD` in the repository `.env`. A run stops before any site starts while one is empty. They are the default accounts of the self-hosted images, listed as `ACCOUNTS` in [the upstream configuration](https://github.com/web-arena-x/webarena/blob/dce04686a56253aefba7b18a4fa0937cf1dc987b/browser_env/env_config.py) |
| Task selection | `evaluation/browser-use/webarena-verified-pilot.json` |

The current pilot has 15 easy, 30 medium and 45 hard tasks. These project-defined tiers use the audited minimum number of visible UI operations; they are different from WebArena's official hard-subset label. Wikipedia and Map are deferred because their local setup needs large external datasets.

<details>
<summary>Apple Silicon setup</summary>

Use a dedicated Colima VZ profile with Rosetta and at least 140 GiB disk:

```bash
colima start browser-eval --vm-type vz --disk 140 --memory 8 --cpus 4 --vz-rosetta
```

The four official amd64 images occupy about 91 GB before writable layers. Legacy Alpine/musl PHP-FPM crashes under Rosetta, so the runner mounts Colima's QEMU interpreter for PHP-FPM in Magento and Reddit. Java, GitLab and the remaining processes use Rosetta. Site data and evaluator logic stay unchanged.

</details>

## Run a model

From the repository root:

```bash
./examples/browser_use/run.sh --model YOUR_MODEL
```

Choose a model your account can access. `BROWSER_USE_MODEL` supplies a default; `--model` overrides it. `./examples/browser_use/run.sh --help` works without credentials or site startup.

To compare another model, use a separate evidence directory:

```bash
./examples/browser_use/run.sh --model YOUR_SECOND_MODEL \
  --output "${BROWSER_EVAL_DATA_HOME:-$HOME/.browser-eval}/browser-use/second-model"
```

One directory contains one model. The runner cannot resume it with another model. Relative `--output` and `--selection` paths resolve from the caller's directory.

### Execution rules

| Rule | Effect |
| --- | --- |
| Reset through evaluation is locked on the shared fixed-port stack | Separate runner processes wait for each other |
| Parallel workers need separate complete site stacks, ports and browser containers | Different output directories alone do not isolate the sites |
| At most one retry after failure | The final execution supplies the task result; all execution time and model usage are retained |

## Build and check the report

After the run, create the deterministic evidence review, build the report and run the fast checks:

```bash
make browser-use-review EVIDENCE="$BROWSER_USE_OUTPUT_DIR"
make browser-use-report EVIDENCE="$BROWSER_USE_OUTPUT_DIR"
make browser-use-report-check EVIDENCE="$BROWSER_USE_OUTPUT_DIR"
```

Before packaging or publishing, run the full gate. It also opens representative pages in the generated languages, both themes, and desktop/narrow viewports:

```bash
make browser-use-release-check EVIDENCE="$BROWSER_USE_OUTPUT_DIR"
```

To compare completed model runs in one report:

```bash
uv run --frozen python -m harness.browser_use report \
  --evidence "$MODEL_A_EVIDENCE" --evidence "$MODEL_B_EVIDENCE" \
  --output reports/browser-use
```

The builder rejects duplicate models and runs with different primary browser/task populations.

## Report rules

These add to the [shared presentation rules](../../.agents/skills/benchmark-report/references/report-and-release.md#shared-presentation-contract-all-benchmark-tracks).

### Results and routes

Browser Use reports expose one manifest-authoritative logical result per browser and task. Dataset routes contain only frozen task definitions and acceptance contracts; result routes own verdicts, timings and trajectories. Keep one filterable result inventory instead of adding duplicate success/failure tables.

Browser Use compares every registered model-by-browser configuration in one same-task matrix generated from the participant registry, whose result cells link to full results. Do not introduce pairwise selectors or duplicate comparison prose when more configurations can be represented as columns. List Agent, model, prompt/tool contract, resources, environment and evaluator as shared controls. Standard summaries use mean, P50 and P95; compare every registered configuration together and mark only globally unique winners on directionally comparable metrics. For action latency, compare all configurations by per-call latency and keep unequal-workload totals and call counts neutral.

### Failure review

Keep automatic evaluator failure location separate from reviewed root cause. A root-cause subtype and reason must come from a per-browser, per-task review bound to the current attempts hash; render both fields blank while that result is unverified, and require complete verified coverage before a formal release when the current profile enables the gate.

Every failed result detail must expose the evidenced failure stage, the programmatic evaluator classification, the exact evaluator/runtime error, the reviewed root cause or its unverified state, the remediation owner/status, a link to the relevant trajectory step, sanitized logs, and the last public trace frames when available. Distinguish a failed BrowserTool action from the last successful action before evaluator rejection. Provide a shared HTML evidence viewer as well as a raw JSON link because static hosts may not preview JSON reliably; load the selected JSON on demand and never duplicate complete evidence payloads into every result page. Extract frames only from ordered Playwright `screencast-frame` events, validate full-page dimensions and provenance, and never treat arbitrary trace resources as screenshots. Label frames as last observed pages, not as exact error screenshots unless the evidence proves that timing.

Keep official scores immutable when diagnosis finds benchmark/evaluator, site, shared-environment, or frozen-reference interference. Quarantine affected tasks across all configurations in a clearly labeled post-hoc diagnostic subset, record remediation in `research/browser-use-remediation.json`, and readmit them only after a preregistered full-scope rerun.
