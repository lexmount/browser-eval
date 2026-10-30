# LexBench-Headless-Browser

A versioned data export of [Lexbench-Headless-Browser](https://github.com/lexmount/Lexbench-Headless-Browser/tree/{{upstream_revision}}). It preserves the original CDP compatibility tasks, web platform tests, scenario specifications, browser and driver configuration, and local fixtures.

The dataset viewer contains **{{upstream_task_count}} executable task definitions** across {{upstream_subset_count}} subsets. The original source files also retain {{upstream_scenario_count}} driver-neutral scenario specifications, {{upstream_fixture_count}} fixture files and {{upstream_config_count}} configuration files. The upstream manifest declares benchmark version `{{upstream_bench_version}}`; its browser binding configuration names {{upstream_driver_count}} driver interfaces. Source commit: [`{{upstream_revision}}`](https://github.com/lexmount/Lexbench-Headless-Browser/commit/{{upstream_revision}}).

## Populations and task contracts

{{dataset_summary}}

The `l1.*` and `l2.*` configurations contain the executable tasks selected by the original manifest's `task_glob` entries. Scenario specifications remain in `definitions/tasks/scenarios/` as design sources and provenance. They are excluded from the viewer population and add no extra tasks to the executable count.

- **L1 — CDP compatibility.** Protocol commands, framework and tool interfaces, navigation, DOM observation, interaction and other browser operations. Some probes are diagnostic and accept completion without grading returned values; the original tags, optional steps and checks state that scope.
- **L2 — Web platform behavior.** Browser semantics checked against local fixture pages. The manifest, capability map and driver bindings define which combinations are eligible.
- **Scenario specifications.** Driver-neutral steps, checks and expected answers. A scenario may have several executable driver translations, so repeated concepts across configurations are intentional.

The declared drivers include Playwright, Puppeteer, Chrome Remote Interface, CDP Use, Pydoll, Stagehand, Chrome DevTools MCP, chromedp, Rod, Chromiumoxide, Ferrum, agent-browser and Selenium. The source manifest also includes raw CDP probes. Driver configuration is a compatibility contract; it does not establish that every driver works with every browser or that a particular run passed.

## Files and fidelity

`definitions/` keeps the original paths and bytes from the source commit:

| Path | Contents |
| --- | --- |
| `manifest.json` | Benchmark version, layers, subsets, task globs, browser policy and fixture site contract |
| `harness_pins.json` | Upstream runtime dependency pins |
| `tasks/` | Original executable JSON tasks and scenario specifications, including assertions and source wording |
| `config/` | Browser/driver bindings, capability maps, schemas and CDP waivers |
| `fixtures/` | Local pages, scripts and assets used by the tasks |
| `LICENSE`, `THIRD_PARTY_NOTICES.md` | Original license and attribution notices |

The additive `definitions/registry.json` binds viewer configurations to their hashes, populations and original source paths. `definitions/viewer/` contains JSON projections for the dataset viewer. Each viewer row's `task` field is the original parsed executable task object; `source_path` locates its unchanged JSON file beneath `definitions/`. The export does not translate instructions, change assertions or replace expected answers. `manifest.json` at the repository root records release hashes and source identity; `definitions/manifest.json` remains the original upstream manifest.

Viewer language labels describe the executable task instructions, which are English in this source edition. Fixture content and expected literals may use other languages; their original text is preserved.

The archive `definitions.tar.gz` provides the same definition files. Measured attempts, browser responses, scores, generated reports, private captures, executable runner code and browser binaries have separate owners and are excluded from this dataset.

## Load a pinned dataset

Browser Eval records the selected Hugging Face commit in its data lock. Use that immutable revision for the viewer rows:

```python
import json
from datasets import load_dataset
from harness.benchmarks.data import binding

selected = binding("headless-browser")
rows = load_dataset(
    selected["repository"],
    "l1.raw_cdp",
    revision=selected["revision"],
    split="test",
)
row = rows[0]
task = json.loads(row["task"])
print(row["source_path"], task["task_id"], task["grader"])
```

Inspect the original scenario specifications under `definitions/tasks/scenarios/`. Dataset access is public and requires no account or token. A Hugging Face data commit and an upstream Git code commit identify different things; retain both when recording a run.

## Execute with the original runner

The JSON files use the upstream driver's protocol. Its runner owns fixture routing, scenario expansion, driver adapters and inline grading. Obtain the matching code and follow the [pinned running instructions](https://github.com/lexmount/Lexbench-Headless-Browser/blob/{{upstream_revision}}/docs/RUNNING.md) for dependencies and browser binaries:

Create a fresh checkout at the data's recorded code revision, then place the pinned Hugging Face definitions at the original paths. This example copies only the original-source inventory; the viewer projections and registry stay outside the runner's inputs.

```python
from pathlib import Path
import hashlib
import json
import subprocess

from harness.benchmarks.data import definition_path

definitions = definition_path("headless-browser", ".")
registry = json.loads((definitions / "registry.json").read_bytes())
checkout = Path("/tmp/Lexbench-Headless-Browser")  # Must not already exist.
subprocess.run(["git", "clone", registry["source"]["repository"], str(checkout)], check=True)
subprocess.run(["git", "-C", str(checkout), "checkout", "--detach", registry["source"]["revision"]], check=True)
actual = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
assert actual == registry["source"]["revision"]
for relative, expected in registry["source_files"].items():
    raw = (definitions / relative).read_bytes()
    assert len(raw) == expected["size"] and hashlib.sha256(raw).hexdigest() == expected["sha256"]
    destination = checkout / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
```

Run the original CLI from that checkout using the pinned guide's setup and scope options. The files it reads now come from the selected Hugging Face snapshot, including the original task JSON, fixtures, configuration, manifest and dependency pins.

Check the source manifest and running guide before choosing a scope. Loading this dataset installs neither that runner nor its runtime dependencies. The local fixture server is implemented in [`runner/run.py`](https://github.com/lexmount/Lexbench-Headless-Browser/blob/{{upstream_revision}}/runner/run.py); driver-neutral expansion and platform semantics also depend on the original runner modules and adapters.

[Browser Eval's public-web replay suite](https://huggingface.co/datasets/Lexmount/LexBench-Trajectory-Replay) contains 30 self-authored tasks with its own live website inputs, BrowserTool actions and exact checks. This scenario collection informed their design; none of those complete tasks is included in the Headless executable catalog. Headless execution remains owned by the pinned upstream driver runtime described above. Existing report protocols may select smaller, hash-bound populations such as the historical 372-task protocol; their scores and denominators remain attached to those original runs. The complete source collection published here does not expand an earlier report's measured population.

## License and citation

The upstream project uses [Apache-2.0](definitions/LICENSE). Included WPT and Chromium fixtures retain their BSD-3-Clause notices in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Preserve those notices when redistributing the data.

```bibtex
@misc{lexbench_headless_browser,
  author = {Lexmount},
  title = {LexBench-Headless-Browser},
  year = {2026},
  url = {https://github.com/lexmount/Lexbench-Headless-Browser},
  version = {{{upstream_revision}}}
}
```

When using Browser Eval's separate replay harness, also cite [Browser Eval](https://github.com/lexmount/browser-eval).
