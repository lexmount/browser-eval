# Product extraction benchmark (Zyte)

Lexmount distribution: **LexBench-Zyte-Product-Extraction** (short name: **LB-ZPE**).

[Product extraction benchmark](https://github.com/scrapinghub/product-extraction-benchmark)
was published by Zyte in 2021. This Hub repository distributes its {{total_records}}
archived product pages and original references. Lexmount maintains the distribution
and a separately identified annotation supplement; it is not the original dataset author.
The original source URL and full upstream commit remain part of every release.

Each task asks an extractor to return price, SKU and stock status from the same
frozen HTML. The source URL records provenance; requesting the live website changes
the input.

## Population and references

{{dataset_summary}}

The upstream source is pinned to commit
`cba97d7a8d42aeefd4021bc15d482f297faceaa3`. All 140 compressed HTML files and the
original ground-truth JSON retain their upstream bytes. The runtime manifest
also binds the decompressed HTML, original evaluator and license.

Each viewer record contains the source URL, compressed HTML path, HTML hash,
original `reference`, and separate `source_qualified_reference`. The latter
amends 47 fields on 36 pages following a complete model-assisted source audit.
It is an additional annotation view, not a second population or human gold.
The original reference remains the default for reproducing the upstream score.
[Reference corrections](definitions/reference-corrections.json) retain the old
and accepted values, literal source excerpts, locators and reasons. Provider
outputs, model judgments and measured scores are not part of this dataset.

The original references contain 134 nonempty price fields, 135 nonempty SKU
fields and 140 stock labels, including nine out-of-stock labels. The source-qualified
view has 134 price fields, 140 SKU fields and 140 stock labels, including 11
out-of-stock labels. Missing values are meaningful and must not become zero prices
or automatic in-stock outputs. The original evaluator's stock convention and
identifier matching must be retained when reproducing its results.

## Load frozen inputs

Select a full Hugging Face commit and keep it with the evaluated outputs:

```python
import gzip
import json
from pathlib import Path
from datasets import load_dataset
from huggingface_hub import snapshot_download

revision = "YOUR_FULL_HF_COMMIT"
rows = load_dataset(
    "Lexmount/LexBench-Zyte-Product-Extraction", "zyte-products-140",
    split="test", revision=revision,
)
task = json.loads(rows[0]["task"])
snapshot = Path(snapshot_download(
    "Lexmount/LexBench-Zyte-Product-Extraction", repo_type="dataset", revision=revision,
    allow_patterns=["definitions/**", "manifest.json", "LICENSE", "NOTICE", "THIRD_PARTY.md"],
))
html = gzip.decompress((snapshot / "definitions" / task["html_path"]).read_bytes())
reference = task["reference"]
```

Webstone uses the shared immutable dataset cache and
[data lock](https://github.com/lexmount/webstone/blob/main/src/harness/benchmarks/data-lock.json):

```sh
git clone https://github.com/lexmount/webstone
cd webstone
{{tooling_checkout}}uv sync --frozen --extra web-extract-eval
make pin-dataset DATASET=web-extract REV=YOUR_FULL_HF_COMMIT
uv run --frozen python -m harness.benchmarks.data web-extract
uv run --frozen python -m harness.web_extract.cli prepare
```

The download verifies the archive and every file. A verified cache works offline
with `HF_HUB_OFFLINE=1`. Historical upstream predictions are fetched separately
only by `reproduce`; they are not extraction inputs. Preparation does not call any
extraction provider or require an API key.

## Evaluation

Use the original evaluator's per-field precision, recall and F1 for price, SKU
and stock. Report out-of-stock recognition separately because the original
population is dominated by in-stock pages. Keep transport failures in the
predeclared denominator. A structured JSON response alone does not establish
field correctness.

Record latency for every logical result, physical retry time separately, and
native usage or actual charges where available. API tiers, input limits and
billing units differ. This dataset defines extraction inputs and references;
it contains no vendor ranking. Webstone's
[execution guide](https://github.com/lexmount/webstone/blob/main/examples/web_extract/README.md)
describes native HTML/URL routes and certification.

## License, attribution and revisions

| Component | Publisher and license | Retained notice |
| --- | --- | --- |
| Original 140 archived pages, ground truth, evaluator, README and upstream extractor | Zyte, MIT, Copyright (c) 2021 Zyte | [LICENSE](LICENSE) and [original upstream license](definitions/upstream/LICENSE) |
| Lexmount-authored packaging metadata and source-qualified annotation supplement | Lexmount, Apache-2.0 | [LICENSE-annotations](LICENSE-annotations) and [NOTICE](NOTICE) |
| Content captured from third-party websites | Original website publishers retain their rights | Source URLs and [THIRD_PARTY.md](THIRD_PARTY.md) |

The two Hub license badges describe different components. They do not offer a
choice to relicense Zyte's original material or the captured websites. The upstream
project released its dataset with an MIT notice; this distribution retains that
notice and does not independently establish training or redistribution rights
for every website asset. Annotation amendments do not replace the original labels.

Cite the upstream [product-extraction benchmark](https://github.com/scrapinghub/product-extraction-benchmark)
and identify this dataset's full commit and reference view. For tooling, cite
[Webstone](https://github.com/lexmount/webstone/blob/main/CITATION.cff).
[VERSIONING.md](VERSIONING.md) describes changes to inputs, references and packaging.
No moving branch name identifies an evaluated edition.

[LexBench-Web-Fetch](https://huggingface.co/datasets/Lexmount/LexBench-Web-Fetch)
and [LexBench-WebMainBench](https://huggingface.co/datasets/Lexmount/LexBench-WebMainBench)
cover different retrieval and main-content extraction tasks with separate metrics.
