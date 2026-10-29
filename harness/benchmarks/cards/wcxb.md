# WCXB: qualified test set

WCXB (Web Content eXtraction Benchmark) evaluates main-content extraction and boilerplate removal from saved web pages. This Lexmount distribution contains {{total_records}} source-qualified pages from Murrough Foley's upstream test split, with the original compressed HTML and ground-truth JSON files preserved byte for byte.

Use the same frozen HTML with each extractor, then compare its returned text with the annotated main content. This measures extraction from fixed inputs. The original URLs provide source provenance; fetching those URLs again would create a different experiment.

## Scope and page types

{{dataset_summary}}

The selected pages cover articles, collections, product pages, listings, forums, documentation and service pages. Article prose is only one part of the workload: product specifications, discussion threads and catalog entries expose different extraction errors. Compare results by page type as well as across the complete selected population.

This edition starts from the upstream 511-page test split and excludes seven inputs that fail its source checks:

- `4066`, `4181`, `4285`, `4482`, `4542` and `4922` have empty main-content references.
- `4145` has no visible body in its frozen HTML under the source-qualification check.

The exclusions were determined from source inputs, independently of extractor outputs. Their reasons and original identities are recorded in [definitions/exclusions.json](definitions/exclusions.json). Every retained HTML and ground-truth file keeps its original bytes and source-relative path. This selection changes the test population; it does not revise the retained annotations. Upstream development data is outside this distribution.

English is the primary language of the reference texts. Names, product labels and occasional titles in other languages do not establish a separate evaluation population for those languages.

## Load a task and its HTML

With `datasets` and `huggingface_hub` installed, select an immutable dataset revision:

```python
import gzip
import json
from pathlib import Path
from datasets import load_dataset
from huggingface_hub import snapshot_download

revision = "<full-Hugging-Face-commit>"
rows = load_dataset(
    "Lexmount/lexbench-wcxb", "qualified-test",
    split="test", revision=revision,
)
task = json.loads(rows[0]["task"])
snapshot = Path(snapshot_download(
    "Lexmount/lexbench-wcxb", repo_type="dataset", revision=revision,
    allow_patterns=["definitions/**", "manifest.json", "LICENSE", "NOTICE", "THIRD_PARTY.md"],
))
with gzip.open(snapshot / "definitions" / task["html_path"], "rt", encoding="utf-8") as stream:
    html = stream.read()
reference = task["reference"]
```

The viewer has one `qualified-test` configuration and one `test` split. Its common columns identify the record, source URL, page family and language; `task` contains the complete task payload as JSON. The payload fields are:

| Field | Meaning |
| --- | --- |
| `file_id` | Original upstream page identifier |
| `page_type` | Upstream page-type label |
| `url` | Original page URL, retained as provenance |
| `reference` | Original `ground_truth.main_content` text |
| `with` | Snippets expected in a correct extraction |
| `without` | Boilerplate snippets expected to be absent |
| `html_path` | Path to the original `.html.gz` input under `definitions/` |
| `ground_truth_path` | Path to the original JSON annotation under `definitions/` |

[definitions/tasks.json](definitions/tasks.json) binds the selected records. [definitions/upstream.json](definitions/upstream.json) records the upstream revision and provenance. The release manifest supplies file hashes so an experiment can identify its exact inputs.

## Scoring

Browser Eval maintains the scoring implementation in [wcxb_scoring.py](https://github.com/lexmount/browser-eval/blob/main/harness/web_fetch/wcxb_scoring.py). Its pure metric functions preserve the pinned upstream evaluator's behavior; the original evaluator identity remains part of the provenance. Loading this dataset does not execute upstream Python code.

Word precision, recall and F1 use lowercase Unicode `\w+` tokens. Matching counts repeated occurrences, so overlap is the multiset intersection between predicted and reference tokens. Precision measures how much returned text belongs to the reference; recall measures how much reference text was retained. F1 is their harmonic mean. Report the mean per-page scores and page-type breakdowns with their denominators.

Snippet checks separately measure the fraction of `with` or `without` snippets found by case-insensitive substring matching. Higher required-snippet coverage is desirable; lower forbidden-snippet occurrence is desirable. The upstream helper returns `1.0` for an empty snippet list, so disclose empty declarations separately when interpreting snippet aggregates.

After installing the matching Browser Eval revision, score a saved prediction with the same metric functions:

```python
from pathlib import Path
from harness.web_fetch.wcxb_scoring import word_f1, snippet_check

predicted = Path("prediction.txt").read_text(encoding="utf-8")
precision, recall, f1 = word_f1(predicted, task["reference"])
required_coverage = snippet_check(predicted, task["with"])
boilerplate_occurrence = snippet_check(predicted, task["without"])
```

{{tooling_checkout}}

The [Web Fetch guide](https://github.com/lexmount/browser-eval/blob/main/harness/web_fetch/README.md) describes execution, saved evidence and report generation. A local HTML report is optional. If you choose to share a report, [Artifact Site hosting](https://github.com/lexmount/browser-eval/blob/main/docs/report-hosting.md) is available. Loading the data, scoring predictions and running local comparisons require no upload.

## Interpretation and limitations

Qualification removes empty references and inputs without a visible body. It does not prove that every remaining annotation includes all relevant content. Preserve the selected population and investigate disagreements against the frozen HTML and original reference before proposing a correction.

Word overlap does not measure semantic equivalence, reading order, table structure or visual fidelity. The frozen-input setup does not measure live access, bot protection, network reliability, freshness or interactive browser behavior. Keep latency comparisons tied to the same inputs and declared processing steps. Document any HTML conversion, rendering, output normalization or external network use that an extractor adds.

Results on this qualified selection need their own denominator. They should not be presented as scores on the complete upstream test split or compared directly with a development-set result. Retain the dataset commit, input hashes, extractor version, scoring implementation and failure policy with each experiment.

## Source, attribution and license

The upstream benchmark is [Murrough Foley's Web Content Extraction Benchmark](https://github.com/Murrough-Foley/web-content-extraction-benchmark). This qualified distribution is maintained at [Lexmount/lexbench-wcxb](https://huggingface.co/datasets/Lexmount/lexbench-wcxb); Browser Eval supplies integration and scoring tools.

The upstream dataset is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), with copyright credited to Murrough Foley, 2026. Retain attribution, the license and the disclosed selection changes when redistributing or adapting this edition. Read [LICENSE](LICENSE), [NOTICE](NOTICE) and [THIRD_PARTY.md](THIRD_PARTY.md). Browser Eval's software license is separate from the dataset license.

## Citation

Cite the original benchmark and identify this distribution's immutable dataset revision and qualified scope:

```bibtex
@dataset{foley2026wcxb,
  author = {Foley, Murrough},
  title = {{WCXB}: A Multi-Type Web Content Extraction Benchmark},
  year = {2026},
  url = {https://github.com/Murrough-Foley/web-content-extraction-benchmark}
}
```

If you use Browser Eval for execution, scoring or reports, also cite its [canonical project citation](https://github.com/lexmount/browser-eval/blob/main/CITATION.cff).
