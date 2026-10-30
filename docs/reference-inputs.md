# Frozen reference inputs

Self-authored Web Fetch task definitions are versioned in [LexBench-Web-Fetch on Hugging Face](https://huggingface.co/datasets/Lexmount/LexBench-Web-Fetch). For a new integration, use the independent verifier and materialized export shipped with Browser Eval as described in [benchmark integration](benchmarks.md). The manifests below retain historical experiment bindings; changing them would change those experiments.

To reproduce a historical run, you need its original input files as well as this checkout. Full datasets, pages, documents and rendered references are stored outside Git.

Each `source-inputs.json` lists the required files and hashes. Import verifies those bytes against the frozen dataset; it preserves historical datasets and certificates unchanged.

| File | Use |
| --- | --- |
| `dataset-catalog.json` | Browse task IDs, origins, coverage and definition hashes |
| External `dataset.json` | Run the historical tasks with their full reference text |
| `source-inputs.json` | Check that all required input files match the historical version |

The catalog cannot substitute for the executable dataset.

The paths below are historical logical paths. Their manifests and catalogs are distributed on Hugging Face, not duplicated in this checkout; the import tool resolves them through the hash-bound benchmark binding.

| Dataset | Manifest |
| --- | --- |
| Web Fetch | `evaluation/source-inputs.json` |
| Popular sites | `evaluation/popular-sites/source-inputs.json` |
| Dynamic content | `evaluation/dynamic-content-100-moli-webfetch/source-inputs.json` |
| Access restrictions | `evaluation/risk-control/source-inputs.json` |

A manifest may contain only the full dataset when references are embedded; no artificial source tree is needed.

Default storage:

```text
~/.browser-eval/reference-inputs/<manifest-id>/dataset.json
~/.browser-eval/reference-inputs/<manifest-id>/sources/
~/.browser-eval/reference-inputs/<manifest-id>/list-references/
```

Set `BROWSER_EVAL_DATA_HOME` to change the root. For a manifest-backed experiment, runners and audits use only its verified external tree. They do not fetch missing pages or use a checkout copy.

A historical command may still name `evaluation/dataset.json`: the reader resolves that path to the verified external original. Import it first. Custom datasets without a manifest use the supplied JSON and adjacent `sources/` and `list-references/`.

## Import and verify

Import an approved copy. `--source` points to the directory containing the original `dataset.json`, `sources/` and `list-references/`:

```sh
uv run --frozen python scripts/import_reference_inputs.py \
  --manifest evaluation/source-inputs.json \
  --source /absolute/path/approved-evaluation-copy
uv run --frozen python scripts/import_reference_inputs.py \
  --manifest evaluation/source-inputs.json --check
```

| Import behavior | Result |
| --- | --- |
| Only manifest-listed files are copied | Unlisted logs and other material stay out |
| Missing files, changed hashes, escaping paths or user-created symlinks | Import fails |
| Existing destination | Import fails; `--check` verifies it without changing it |
| Interrupted import | No partial input tree becomes available |

macOS system aliases `/tmp` and `/var` are handled by path normalization.

Source acquisition and usage terms remain those of the original source. These commands do not download third-party data, change its license or grant redistribution rights. Keep private input directories and source copies off GitHub. The code license does not cover third-party reference text.

## Tests and revisions

Ordinary `make ci` runs on a clean checkout without historical snapshots. Historical reconstruction checks identify absent external inputs; synthetic execution, scoring, input-validation and report tests still run. Use strict mode to verify historical references:

```sh
make test-reference-inputs
# Equivalent Python test entry point
uv run --frozen --all-extras python -m pytest tests -q --require-reference-inputs
```

Strict mode verifies every manifest under `evaluation/` before tests and fails if any input set is missing. Corrupt installed inputs fail in ordinary mode too; they are never treated as absent and skipped.

Bound inputs are immutable. Reference-update commands accept separate candidate datasets and source trees. After independent capture, applicable review and regression checks, new manifests and experiment profiles declare a new version. Retain prior inputs, measurements and certificates. Repository-relative paths in historical receipts identify original sources; they no longer imply that those bytes are distributed in the checkout.

Some frozen acceptance and audit artifacts retain Chinese text. Their hashes and, where applicable, compiler-readable rules are experiment inputs. Translating them in place would change the experiment contract. English usage guides describe their role; raw quotations and task text likewise retain their original language.

Earlier source material remains in Git history. Removing current files does not clean historical commits; public history must follow the same distribution boundaries.
