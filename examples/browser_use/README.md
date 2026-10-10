# Run Browser Use

CowAgent makes the decisions; WebArena-Verified checks task completion. Use this track to compare browsers with the same Agent, model and tools. For fixed actions without Agent decisions, use [trajectory replay](../trajectory_replay/README.md).

## Prepare the environment

| Needed | Where to configure it |
| --- | --- |
| Clean CowAgent checkout | `COWAGENT_REPO` in the repository `.env`; the runner records its commit and refuses uncommitted changes |
| Model account | `OPENAI_API_KEY`, optional `OPENAI_BASE_URL`, in `.env` |
| WebArena sites | Shopping, Shopping Admin, Reddit and GitLab |
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
uv run --frozen python scripts/build_browser_use_report.py \
  --evidence "$MODEL_A_EVIDENCE" --evidence "$MODEL_B_EVIDENCE" \
  --output reports/browser-use
```

The builder rejects duplicate models and runs with different primary browser/task populations.
