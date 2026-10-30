# LexBench-Trajectory-Replay

Browser Eval authored this public-web replay suite to compare browsers using fixed actions and deterministic checks. The tasks cover reading public pages, forms, navigation history, delayed elements, search, and scrolling. Browser Eval executes the declared actions and checks the saved outputs against explicit assertions.

This dataset contains {{total_records}} task records with public website URLs, BrowserTool actions and exact expected outputs. The task instructions are in Chinese. Some references contain English page headings, interface text, or quotations; the source wording is preserved.

{{dataset_summary}}

## Evaluation flow

| Stage | What happens |
| --- | --- |
| Inputs | Select the task, fixed actions and assertions. |
| Replay | Run the declared actions through the browser adapter. |
| Scoring | Check saved outputs in code and retain scores and evidence. |
| Optional report | Build HTML locally from saved evidence. Share only on request and with authorization. |
| Optional verification | Review programmatic results and evidence with an LLM verifier or human sampling. |

The browser receives commands such as navigate, click, fill, read text, or move through history. An LLM does not select the next action. A task passes when its required outputs satisfy its assertions. Optional LLM diagnostics provide another review of the evidence; they do not replace the programmatic result.

## Contents

Each record includes a stable string ID, a readable title, difficulty and tags, fixed tool calls, assertions, and source attribution. Calls name their saved outputs so an assertion can refer to the exact value it checks. The runtime suite also records the expected action sequence and deterministic ground truth.

The canonical runtime file is `definitions/suites/curated-v1.json`. The dataset viewer exposes the same tasks in `definitions/datasets/curated-v1.json`, with language metadata for discovery. `definitions/registry.json` binds each configuration to its file hash and population. The publication bindings record the source suite hash; this edition preserves the original runtime suite bytes. Expected outputs are explicit task assertions; prior run responses are not part of this suite.

The task definitions cite [Headless Browser's scenario collection](https://github.com/lexmount/Lexbench-Headless-Browser/tree/3f1580a20922fc3f339b5fc279d9bbdb06d5d194/tasks/scenarios) as a design reference. Three definitions cite individual scenarios: cascading search and document version/language switching draw on select-option checks; infinite scrolling draws on progressive-list and paging checks. These tasks use their own public pages, actions and expected answers. The remaining definitions cite the collection as a whole. Each record retains its attribution.

No complete task is imported from [LexBench-Headless-Browser](https://huggingface.co/datasets/Lexmount/LexBench-Headless-Browser) or [LexBench-WebArena-Replay](https://huggingface.co/datasets/Lexmount/LexBench-WebArena-Replay). Headless tasks use local fixtures or blank pages and their original CDP/driver checks; WebArena tasks use self-hosted applications and official evaluators. Shared interaction patterns, such as selecting an option, are design similarities across distinct task inputs and acceptance contracts.

## Use with Browser Eval

Get the [Browser Eval code](https://github.com/lexmount/browser-eval) and follow the [trajectory replay instructions](https://github.com/lexmount/browser-eval/blob/main/examples/trajectory_replay/README.md):

```bash
./examples/trajectory_replay/run.sh
```

Add `--no-report` to that command to retain scores and evidence without building local HTML.

The supported runner resolves its default task suite from this dataset's immutable revision recorded in Browser Eval's data lock. The first load downloads the public definition bundle; subsequent loads use the verified local cache. Reading the dataset requires no Hugging Face account or token. An explicit suite path remains available for reproducing an older input bundle.

For inspection from the checked-out project:

```python
from harness.trajectory_replay.suite import load_suite

suite = load_suite()
print(suite.tasks[0].title)
print(suite.tasks[0].tool_calls)
```

Use the revision recorded in the code when reproducing a published comparison. Qualification receipts and measured attempts remain bound to their original input hashes. A new input revision does not update those receipts.

## Results and limits

The dataset contains benchmark inputs. Browser images, qualification evidence, measured responses, screenshots, and generated reports have their own release bindings. A run needs the required browser runtime and qualification material described in the reproduction guide.

The JSON freezes actions and expected outputs, while the target websites remain live. Content, availability, and page structure can change even when the task file and browser binary are pinned. A failure may therefore require inspecting the saved response and action trace before attributing it to the browser.

Several tasks use the same website, and simpler checks also appear inside longer workflows. The task count does not represent independent websites or unrelated scenario families.

The suite measures fixed-action execution and retrieval. It does not measure an agent's planning or its ability to recover by inventing a different sequence. Browser Eval's experimental Browser Use track has a separate execution contract.

Optional local HTML reports turn saved evidence into results readers can inspect and rebuild. Share a report only when publication is explicitly requested and authorized. Reports support drilldown to individual results and their raw responses, action traces, logs, and scoring evidence. Optional verification and human sampling help review those results without requiring every case to be read manually.

## Citation and attribution

Please cite Browser Eval when using these task definitions or the replay harness:

```bibtex
@misc{browser_eval,
  author = {Lexmount},
  title = {Browser Eval: Benchmarking Browsers and Web Tools for AI Agents},
  year = {2026},
  url = {https://github.com/lexmount/browser-eval}
}
```

This release distributes compact task definitions and checks. Linked websites and documents retain their respective terms; their captured pages and measured outputs are not included in this dataset.
