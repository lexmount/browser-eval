# LexBench-SimpleQA-Verified

A {{total_records}}-question retrieval edition of [Google SimpleQA Verified](https://huggingface.co/datasets/google/simpleqa-verified), used by [Browser Eval](https://github.com/lexmount/browser-eval) to compare search tools. It retains publisher questions, reference answers and supporting URLs so a search result list can be checked for reference-page retrieval and answer-bearing text.

Google DeepMind and Google Research designed the original benchmark to measure a model's factual knowledge without search tools. This derivative changes the task to retrieval evaluation. Its retrieval metrics are not official SimpleQA Verified accuracy or evidence of a model's closed-book factual knowledge.

{{dataset_summary}}

This dataset owns the factual retrieval inputs used alongside [LexBench-Web-Search](https://huggingface.co/datasets/Lexmount/LexBench-Web-Search). Web Search references these definitions through its composition descriptor; it does not publish another copy of the questions or source receipt.

## Selection and changes

The `factual-v1` configuration contains every question in the pinned 1,000-row publisher CSV whose `problem` has at most 128 Unicode code points, measured by Python's `len()` on the decoded string. This selects {{total_records}} questions; longer questions are excluded before collection. There is no truncation, rewriting or selection based on search results.

Publisher row order, question text and reference answer text are retained. Case IDs use `sqv-` followed by the publisher's `original_index`, preserving the link to OpenAI SimpleQA. Supporting URLs are split from the CSV field, deduplicated in order, and stripped of unmatched trailing closing parentheses. Topic and answer-type labels are retained. The publisher's `multi_step` and `requires_reasoning` fields are not part of these derived task records.

[The source manifest](definitions/factual-source.json) records the exact publisher CSV revision and SHA-256, selection rule and expected counts. [The selection implementation](https://github.com/lexmount/browser-eval/blob/main/src/lexbench_websearch/simpleqa.py) rebuilds the population from that input. This release contains no search-provider rankings, relevance grades or measured results.

## Data and schema

| Resource | Contents |
| --- | --- |
| `data/factual-v1.jsonl` | Dataset-viewer rows: `id`, `input`, `family`, `language`, `task` |
| `definitions/datasets/factual-v1.json` | Complete task records and source-reference bindings |
| `definitions/factual-source.json` | Upstream input and deterministic selection contract |
| `manifest.json` | Release source commit, file sizes and SHA-256 hashes |

The viewer's `input` is the unchanged question. Parse `task` as JSON for `id`, `query`, `reference_answer`, `reference_urls`, `topic`, `answer_type`, provenance and derivation. Records have `family: web`, `category: factual` and `language: en`.

## Load and evaluate

Choose a full Hugging Face commit from this dataset's revision history and retain it with your evaluation:

```python
import json
from datasets import load_dataset

rows = load_dataset(
    "Lexmount/LexBench-SimpleQA-Verified",
    "factual-v1",
    split="test",
    revision="YOUR_FULL_HF_COMMIT",
)
task = json.loads(rows[0]["task"])
query = task["query"]
answer, supporting_urls = task["reference_answer"], task["reference_urls"]
```

`definitions.tar.gz` contains the same definition bytes as `definitions/`. Browser Eval's [data lock](https://github.com/lexmount/browser-eval/blob/main/harness/benchmarks/data-lock.json) selects the installed snapshot. Follow the [Web Search guide](https://github.com/lexmount/browser-eval/blob/main/harness/web_search/README.md) to declare a profile, collect provider responses and build a report with saved evidence. The certified collector is `scripts/run_certified_benchmark.py web-search`.

Reference Hit@k tests whether a supplied supporting URL appears in the first k results; MRR@k measures the first such hit's reciprocal rank. Answer Match@k checks answer cues in returned text. Source references are not an exhaustive set of relevant pages, and lexical answer matches do not establish that a result correctly answers the question. Report these measures separately from graded relevance. Searchable copies of the dataset can also create contamination; Browser Eval exposes a separate contamination diagnostic while retaining the original results.

## License and citations

The Google dataset card declares MIT. The derived definitions retain attribution to Google DeepMind / Google Research and to the original OpenAI SimpleQA, copyright 2024 OpenAI. Project-authored additions use Apache-2.0. See [LICENSE](LICENSE), [THIRD_PARTY.md](THIRD_PARTY.md) and [the retained notices](definitions/NOTICE.txt). Supporting website URLs do not grant a license to redistribute the referenced pages.

Cite both upstream works when using this derivative, and identify the selected dataset revision:

> Haas, Yona, D'Antonio, Goldshtein and Das (2025). [SimpleQA Verified: A Reliable Factuality Benchmark to Measure Parametric Knowledge](https://huggingface.co/datasets/google/simpleqa-verified).
>
> Wei et al. (2024). [Measuring Short-Form Factuality in Large Language Models](https://github.com/openai/simple-evals). OpenAI SimpleQA.

For the harness, cite [Browser Eval: Benchmarking Browsers and Web Tools for AI Agents](https://github.com/lexmount/browser-eval/blob/main/CITATION.cff).

[LexBench-Web-Search](https://huggingface.co/datasets/Lexmount/LexBench-Web-Search) contains the separate scenario-query suites. [LexBench-Web-Fetch](https://huggingface.co/datasets/Lexmount/LexBench-Web-Fetch) and [LexBench-WebMainBench](https://huggingface.co/datasets/Lexmount/LexBench-WebMainBench) evaluate fetching and extraction with their own task populations.
