# Frozen reference inputs

Complete historical datasets, source pages, documents, list references and independently rendered snapshots live outside Git. Each directory's `source-inputs.json` binds the complete file inventory, per-file SHA-256 values and frozen dataset hash. Full datasets and historical certificates retain their original bytes.

`dataset-catalog.json` contains task inventories, origins, coverage plans and definition hashes. It contains no full reference text and cannot be passed to the runner as an executable dataset.

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

Set `BROWSER_EVAL_DATA_HOME` to change the data root. When a manifest declares an external tree, runners, reference prechecks and audits read that tree only. They do not fall back to a checkout copy or fetch websites automatically.

Historical commands naming `evaluation/dataset.json` use the shared reader to resolve and hash-check the external original; that file is no longer in the checkout. Import missing inputs before running. A task catalog cannot replace the full dataset. Custom datasets without an external manifest use the supplied JSON and its adjacent `sources/` and `list-references/`.

## Import and verify

Import an approved copy. `--source` points to the directory containing the original `dataset.json`, `sources/` and `list-references/`:

```sh
uv run --frozen python scripts/import_reference_inputs.py \
  --manifest evaluation/source-inputs.json \
  --source /absolute/path/approved-evaluation-copy
uv run --frozen python scripts/import_reference_inputs.py \
  --manifest evaluation/source-inputs.json --check
```

Import copies only declared files, excluding undeclared logs and material. The destination must be new; checking does not modify existing files. Missing files, hash mismatches, escaping paths and user-created symlinks are rejected. macOS system aliases `/tmp` and `/var` are accepted through shared path normalization. The new input directory becomes available only after complete verification; interruptions leave no partial import.

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
