# LexBench-Web-Search

LexBench-Web-Search evaluates the pages a search API returns for fixed queries: whether useful results appear near the top, whether explicit source requirements are met, and whether known reference pages are found. The tasks cover general web search, named-site search and vertical search across English, Chinese and other languages.

[Browser Eval](https://github.com/lexmount/browser-eval) maintains the scorer, provider adapters and evidence reports. This dataset supplies versioned query definitions, source attribution and qualified reference inputs. Use it to evaluate retrieval before an agent fetches pages or generates an answer. The harness turns saved results into HTML reports with query-level drilldown that readers can inspect, rebuild and share.

## Dataset editions

{{dataset_summary}}

The scenario editions retain the same query population. Their reference qualifications and URL equivalences are versioned; they are not independent samples to concatenate. The [separate SimpleQA Verified retrieval edition](https://huggingface.co/datasets/Lexmount/LexBench-SimpleQA-Verified) supplies factual questions, original answers and supporting URLs. Report its retrieval metrics separately from scenario relevance.

Each scenario includes an ID, query, language and search family. Where available, it also carries an information need, known-page anchors, source origin, derivation and reference qualifications. `origin` and `provenance_kind` distinguish copied queries, adaptations and authored controls. Reference observations retain their scope and evidence bindings.

## Evaluation

Send the same query to every participant with a predeclared result budget, normally five. Preserve the provider's ranking, titles, URLs and snippets. Collection calls the search APIs directly; an agent does not rewrite queries or choose additional searches during the comparison.

Pool and deduplicate returned candidates per query, then collect human or model relevance grades. The preview protocol grades titles and snippets with provider names, original ranks and source URLs withheld; programmatic source-eligibility checks enforce explicit query requirements. Grades describe the returned preview:

| Grade | Meaning |
| ---: | --- |
| 0 | Irrelevant, or violates a required site, time or content constraint |
| 1 | Related to the topic but insufficient for the request |
| 2 | Provides useful supporting evidence |
| 3 | Directly meets the core request |

This grade structure follows [TREC Deep Learning](https://microsoft.github.io/msmarco/TREC-Deep-Learning.html); these queries and task definitions form a separate benchmark. Historical model grades are not independent human ground truth, and this dataset does not ship a universal relevance judgment for every page on the web.

Given a frozen candidate pool and complete grades, the scorer computes:

- **nDCG@k:** ranking quality using gain `2**grade - 1`, normalized against the same pool's ideal ranking.
- **Precision@k:** distinct results graded at least 2, divided by the declared cutoff.
- **Direct-result success@k:** whether any returned result has grade 3.

Duplicates add no gain; missing positions contribute zero. Queries with an all-zero pool stay in the population with nDCG zero. Aggregate over all declared queries and disclose the population. Known-page hits, reciprocal rank, source-domain checks and duplicate rate remain separate diagnostics. Anchor lists cover selected known pages; useful results outside those lists can still receive relevance credit.

The [Search guide](https://github.com/lexmount/browser-eval/blob/main/harness/web_search/README.md) covers annotation receipts, source rules, retries, timing, billing and optional independent review. The [benchmark guide](https://github.com/lexmount/browser-eval/blob/main/docs/benchmarks.md) explains dataset/scorer identities and reproduction.

## Use the data and scorer

Clone [Browser Eval](https://github.com/lexmount/browser-eval), install its locked environment, then inspect or export the selected definitions:

```sh
{{tooling_checkout}}uv sync --frozen
uv run --frozen lexbench-websearch list
uv run --frozen lexbench-websearch validate scenarios
uv run --frozen lexbench-websearch export scenarios > /tmp/scenarios.json
```

Browser Eval's [data lock](https://github.com/lexmount/browser-eval/blob/main/harness/benchmarks/data-lock.json) selects exact Hugging Face commits and file hashes. The loader verifies those inputs; inspection and offline scoring require no search-provider key. Live collection uses the credentials and explicit profile described in the Search guide.

For supplied ranking labels, the same deterministic metric implementation is available in Python:

```python
from lexbench_websearch.relevance import query_metrics

metrics = query_metrics(["useful", "unrelated"], {"useful": 3, "unrelated": 0}, cutoff=5)
assert metrics["precision"] == 0.2
```

Freeze returned responses and grades to recompute scores and rebuild reports without new provider calls. Live rankings and model annotation can change even when the input queries remain fixed.

## Published use

The [October 22, 2026 search benchmark](https://browser.lexmount.com/blog/websearch-benchmark) compares four SERP services using top-five results. Its scenario evaluation reports Lexmount at 64.0 nDCG@5 and Serper at 64.4, on the article's 0 to 100 display scale, alongside latency and price. Query examples show how grades expose irrelevant results and missed source requirements. The factual comparison uses the separate SimpleQA retrieval inputs, with distinct quality and latency populations. These results describe that collection and protocol; use the article's examples to inspect scoring decisions before designing another comparison.

## Sources, attribution and limits

The scenario inventory retains provenance for 80 queries from [Microck/websearch-bench](https://github.com/Microck/websearch-bench), 7 exact community queries, 28 screenshot transcriptions, 14 community-derived queries and 20 curated queries. The [pinned upstream query file](https://github.com/Microck/websearch-bench/blob/7714e86b1b8d7b943037c38b04d4f6ba65566d5b/data/queries.jsonl) is MIT licensed. Its query text and intent are retained; upstream provider judgments and scores are not imported. Adaptations and authored additions carry their own derivation records.

The screenshot transcriptions have no established original author or public post. They must not be attributed to a verified social-media author. Community posts retain their own terms; screenshots and full third-party articles are not redistributed. The [original attribution inventory and notices](https://github.com/lexmount/lexbench-websearch/blob/0e4c0cd7d828dc80f0a701886d208cc6c0d598e9/THIRD_PARTY.md) document these distinctions; the published definitions retain `sources.json` and `NOTICE.txt`.

The companion factual edition derives from [Google's SimpleQA Verified](https://huggingface.co/datasets/google/simpleqa-verified), described by Haas et al. in [SimpleQA Verified: A Reliable Factuality Benchmark to Measure Parametric Knowledge](https://huggingface.co/datasets/google/simpleqa-verified), which builds on [OpenAI SimpleQA](https://github.com/openai/simple-evals) and its [paper](https://github.com/openai/simple-evals). The retrieval selection keeps questions of at most 128 Unicode characters. Question and answer text remain unchanged; supporting URL parsing removes unmatched closing parentheses. Both upstream datasets declare MIT licensing. This length-qualified derivative measures retrieval diagnostics; the official benchmark evaluates closed-book factuality. The independent [retrieval dataset card](https://huggingface.co/datasets/Lexmount/LexBench-SimpleQA-Verified) provides its selection and licensing details.

Thirteen scenario queries mention DeepSeek, reflecting documentation and community sources. The query mix provides uneven topic and language coverage. Known-page references omit other potentially useful pages. Preview relevance does not establish page truth, and lexical answer matches do not prove entailment. Publish source composition, grading scope and the chosen cutoff with comparisons; keep scenario and factual scores separate.

Project code is Apache-2.0. Dataset material retains its source licenses and notices; that code license does not relicense community content.

## Citation

Cite this component and the upstream datasets you use:

```bibtex
@misc{lexbench_websearch_2026,
  author = {{LexBench Web Search contributors}},
  title = {Browser Eval: Benchmarking Browsers and Web Tools for AI Agents},
  note = {Web Search benchmark component},
  year = {2026},
  url = {https://huggingface.co/datasets/Lexmount/LexBench-Web-Search}
}
```
