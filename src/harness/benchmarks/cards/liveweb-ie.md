# LiveWeb-IE

Short name: **LB-LWIE**.

This research distribution contains {{total_records}} source-qualified pages sampled from [LiveWeb-IE](https://github.com/sbY99/LiveWeb-IE). It retains the original benchmark name and attribution. It is a selected evaluation cohort. Scores from this selection do not reproduce the paper's published results.

LiveWeb-IE: A Benchmark For Online Web Information Extraction (ICLR 2026), https://arxiv.org/abs/2603.13773.

## Webstone

This dataset is integrated with [Webstone](https://github.com/lexmount/webstone). The [Web Extract guide](https://github.com/lexmount/webstone/blob/main/examples/web_extract/README.md) covers dataset selection, hosted or live inputs, provider selection and certified collection. The upstream benchmark links and original license below remain authoritative for the source data.

```sh
git clone https://github.com/lexmount/webstone
cd webstone
{{tooling_checkout}}uv sync --frozen --extra web-extract-eval
```

## Inputs and references

`definitions.tar.gz` contains the source-qualified task definition, original HTML, captured resources, references and provenance. `manifest.json` binds the complete archive and every file by SHA-256. Original task inputs and reference values are unchanged from the preceding edition. Webstone uses the shared immutable dataset cache; no extraction service is called during preparation.

```sh
make pin-dataset DATASET=liveweb-ie REV=YOUR_FULL_HF_COMMIT
uv run --frozen --extra web-extract-eval python -m harness.web_extract.cli \
  --home /absolute/data/liveweb-ie prepare --dataset liveweb-ie
```

The selected input mode is `live`. Extraction services visit the original live URLs. Saved HTML is private source-validation evidence. It must not substitute for the original website. Original reference values are checked before and after collection.

## Scoring

The comparison uses value-level micro F1, page-hit macro F1 and exact-field-set rate. Original references are retained alongside source-qualified whitespace/entity and source-case normalization. Declared URL fields use URL equivalence. These are evaluation adaptations; this distribution does not claim the original paper's protocol.

## Rights

LiveWeb-IE by Yang et al., 2026.
The LiveWeb-IE benchmark is released strictly for **non-commercial research purposes only**.

By downloading or using this dataset, you agree to the following terms:
1.  **Non-Commercial Use**: You may not use this dataset for any commercial purposes, including but not limited to training commercial models or enhancing commercial services.
2.  **Attribution**: Any publication or report using this dataset must cite the original paper (LiveWeb-IE: A Benchmark For Online Web Information Extraction, ICLR 2026).
3.  **No Redistribution without License**: You may not redistribute this dataset without including this license agreement.

This restriction complies with the ethical agreements made with the website providers included in this benchmark.



Website content retains its original rights. This repository is private. Packaging does not broaden the upstream license or grant rights to the original website content.
