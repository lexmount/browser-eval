# Expanded SWDE

Short name: **LB-ESWDE**.

This research distribution contains {{total_records}} source-qualified pages sampled from [Expanded SWDE](https://www.colinlockard.com/expanded_swde.html). It retains the original benchmark name and attribution. It is a selected evaluation cohort. Scores from this selection do not reproduce the paper's published results.

Lockard et al., OpenCeres (2019), extending SWDE by Hao et al. (2011).

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
make pin-dataset DATASET=expanded-swde REV=YOUR_FULL_HF_COMMIT
uv run --frozen --extra web-extract-eval python -m harness.web_extract.cli \
  --home /absolute/data/expanded-swde prepare --dataset expanded-swde
```

The selected input mode is `snapshot`. Native extraction services visit a separately deployed, version-bound HTML service. The deployment exposes pages and resources only; reference answers remain outside the hosted page service.

## Scoring

The comparison uses value-level micro F1, page-hit macro F1 and exact-field-set rate. Original references are retained alongside source-qualified whitespace/entity and source-case normalization. Declared URL fields use URL equivalence. These are evaluation adaptations; this distribution does not claim the original paper's protocol.

## Rights

Expanded SWDE by Lockard et al. (OpenCeres, 2019), extending SWDE by Hao et al. (2011). Original SWDE archive: Microsoft Reciprocal License; webpage content retains original rights. Preserve both source attributions. No blanket MIT or Apache license is granted to website content.

Website content retains its original rights. This repository is private. Packaging does not broaden the upstream license or grant rights to the original website content.

## Public HTML snapshots

[snapshots.json](snapshots.json) binds every page ID to its SHA-256 and an immutable public deployment. Native extraction APIs receive `fixture_base + '/' + id`; the original HTML files remain in this repository.

Original files are authoritative. Publication verifies the declared dataset version and complete input inventory before adding this binding. Changed HTML requires a new deployment and a new HF commit; previous files and deployment URLs remain available. Pin the full HF commit for evaluation. Managed immutable deployments are checked through the whole-dataset manifest before and after calls; external hosts have every page byte-checked when first bound. A mismatch stops collection rather than silently replacing the input. The navigation index does not host dataset HTML.
