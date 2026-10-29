# LexBench WebMainBench

A reference-corrected edition of [OpenDataLab WebMainBench](https://huggingface.co/datasets/opendatalab/WebMainBench) with {{total_records}} frozen extraction tasks, used by [Browser Eval](https://github.com/lexmount/browser-eval). Each task supplies frozen HTML and a reference Markdown answer, so extractors can receive the same page bytes. This edition supports comparisons of main-content extraction, HTML-to-Markdown conversion and preservation of code, formulas and tables.

{{dataset_summary}}

## What changed from upstream

The `corrected-545` configuration retains all {{total_records}} pages in their original order, with unchanged `track_id`, URL and input HTML. It uses the upstream 545-page subset with calibrated references; it does not contain the full 7,809-page WebMainBench corpus.

The correction receipt records 22 corrections: 19 feature-scope metadata changes and three replacement reference texts. One replacement removes duplicated hidden editor content; two preserve selected inline code as inline code in the Markdown reference. Read the per-case rationale and source hashes in [the correction manifest](definitions/corrections/WebMainBench_545.json); the three replacement Markdown files are beside it. [Upstream provenance](definitions/upstream.json) binds the original corpus, the corrected file and the matching correction/scorer revision.

Browser Eval's [WebMainBench fork](https://github.com/lexmount/WebMainBench/tree/lex-main) also carries scoring corrections for component applicability and aggregation. Dataset and scorer revisions must be recorded together. Results from this edition need to name the corrected edition and evaluation configuration; they are distinct from the upstream leaderboard.

## Data and schema

| Resource | Contents |
| --- | --- |
| `data/corrected-545.jsonl` | Dataset-viewer rows: `id`, `input`, `family`, `language`, `task` |
| `definitions/WebMainBench_545.jsonl` | Complete corrected source records, one JSON object per line |
| `definitions/corrections/` | Correction receipt and three replacement references |
| `definitions/upstream.json` | Source revision, corpus hashes and correction/scorer revision |
| `manifest.json` | Release source commit, file sizes and SHA-256 hashes |

The viewer's `id` is the original `track_id`; `input` is the page URL. Parse `task` as JSON for the full record: `track_id`, `url`, `html`, `main_html`, `convert_main_content`, `groundtruth_content` and `meta`. Metadata includes the original page language and content-feature annotations. The viewer's `language` describes the frozen reference text: reviewed labels fill missing annotations and correct demonstrated source errors; `mul` marks references with substantial content in multiple languages. Original `meta.language` values remain unchanged inside `task`. Language-annotation provenance is recorded in `manifest.json`.

`html` is the frozen extraction input. `groundtruth_content` is the corrected evaluation reference. URLs identify the source pages; fetching those live URLs produces a different input from replaying the saved HTML.

## Load and evaluate

Choose a full Hugging Face commit from this dataset's revision history and retain it with your results:

```python
import json
from datasets import load_dataset

rows = load_dataset(
    "Lexmount/lexbench-webmainbench",
    "corrected-545",
    split="test",
    revision="YOUR_FULL_HF_COMMIT",
)
page = json.loads(rows[0]["task"])
html, reference = page["html"], page["groundtruth_content"]
```

For a raw-file download, `definitions.tar.gz` contains the same definition bytes as `definitions/`; verify them against `manifest.json`. Browser Eval selects its dataset and scorer through the [data lock](https://github.com/lexmount/browser-eval/blob/main/harness/benchmarks/data-lock.json). Its [benchmark guide](https://github.com/lexmount/browser-eval/blob/main/docs/benchmarks.md#webmainbench-runtime) explains the pinned runner and scorer. Execution starts at `scripts/run_webmainbench_benchmark.py`; `scripts/score_webmainbench_results.py` scores saved outputs.

The 545-page scorer measures text, code, formula and table edit similarity, plus table structural similarity (TEDS). Report the components, their applicable populations and the selected aggregation. The full upstream corpus's ROUGE evaluation is a separate protocol. Provider outputs and measured scores are not included in this dataset.

## License and attribution

OpenDataLab WebMainBench and this edition's project-authored changes use Apache-2.0; see [LICENSE](LICENSE), [NOTICE](NOTICE) and [THIRD_PARTY.md](THIRD_PARTY.md). The package includes upstream website HTML and reference text. Website content retains its source rights; the repository license does not establish blanket rights to reuse every captured page for model training.

Please cite the upstream work and identify this corrected dataset revision:

> Liu et al. (2025). [Dripper: Token-Efficient Main HTML Extraction with a Lightweight LM](https://huggingface.co/datasets/opendatalab/WebMainBench).

For the execution and reporting harness, cite [Browser Eval: Benchmarking Browsers and Web Tools for AI Agents](https://github.com/lexmount/browser-eval/blob/main/CITATION.cff).

Related datasets: [LexBench Web Fetch](https://huggingface.co/datasets/Lexmount/lexbench-webfetch) covers additional fetch tasks; [LexBench Web Search](https://huggingface.co/datasets/Lexmount/lexbench-websearch) and its separate [SimpleQA Verified retrieval edition](https://huggingface.co/datasets/Lexmount/lexbench-simpleqa-verified) cover search. Their task populations and evaluation contracts are separate from these extraction pages.
