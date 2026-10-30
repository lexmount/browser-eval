# Try Web Fetch on a local page

Run HTTP, Moli or Chromium against the same short article. A complete response passes; a missing paragraph fails. This checks the adapter, scoring and report pipeline. The article is not a browser performance benchmark.

Run commands from the repository root after the [base setup](../../README.md#quickstart).

## Start the page and run a method

Keep the page server running in a separate terminal:

```bash
uv run --frozen python -m http.server 8765 --bind 127.0.0.1 \
  --directory examples/web_fetch/site
```

In another terminal at the repository root, validate and run the HTTP baseline:

```bash
uv run --frozen browser-harness-eval validate \
  --dataset examples/web_fetch/dataset.json \
  --provider-config configs/providers.quickstart.json --providers http
uv run --frozen browser-harness-eval run \
  --dataset examples/web_fetch/dataset.json \
  --provider-config configs/providers.quickstart.json --providers http --delay 0
```

The article should pass. `http` reads response text without executing JavaScript.

## Switch browsers

Replace `--providers http` in both commands with your chosen method, or use `--providers http,moli,chromium` to compare several. Only selected methods are initialized.

| Method | Setup |
| --- | --- |
| `moli` | Install a [Moli release](https://github.com/lexmount/moli/releases); put `moli` on PATH or set `MOLI_BINARY` to its absolute path |
| `chromium` | Install the dependencies below; optionally set `CHROMIUM_BINARY` |
| `lightpanda` | Install its binary and set `LIGHTPANDA_BINARY`, or put it on PATH |
| `obscura` | Install its binary and set `OBSCURA_BINARY`, or put it on PATH |

All four use the [quickstart configuration](../../configs/providers.quickstart.json). For Chromium, run:

```bash
uv sync --frozen --extra headless-eval
uv run --frozen playwright install chromium
npm ci
```

Use Node.js 22. If `CHROMIUM_BINARY` is unset, the adapter can use Playwright's installed Chromium.

Quickstart records the actual browser versions; it does not enforce historical pins. Formal comparisons must declare versions before calls. Keep the local server bound to loopback. This static page checks integration, not dynamic-page capability.

## Choose tasks and methods

Copy `dataset.json` and `configs/providers.quickstart.json` for your own experiment, then pass their paths explicitly:

```bash
uv run --frozen browser-harness-eval run \
  --dataset /absolute/path/dataset.json \
  --provider-config /absolute/path/providers.json --providers method-a,method-b \
  --case-ids case-a,case-b --task-kind body \
  --output /absolute/path/new-evidence
```

| Option | Meaning |
| --- | --- |
| `--providers` | Keys from your provider configuration; only selected methods are initialized |
| `--case-ids` | Task IDs from your dataset |
| `--task-kind` | `body`, `structure`, `resource` or `file`; combined with task IDs, selects their intersection |
| `--output` | A new evidence directory; existing directories are rejected |

Declare references and acceptance rules before running. See [metrics](../../docs/metrics.md) and the [adapter contract](../../docs/provider-contract.md) for dataset and method requirements. These CLI runs are diagnostic; formal comparisons use the [certified track pipeline](../../src/harness/web_fetch/README.md).

## Open the report

The terminal prints the absolute path to `report/index.html`. It sits inside the run directory, next to `results.jsonl`, `summary.json`, `manifest.json`, `dataset.json` and `artifacts/`. Outputs default to `~/.browser-eval/runs/`; `BROWSER_EVAL_DATA_HOME` changes the data root.

Rebuild from saved evidence without calling the methods again:

```bash
uv run --frozen browser-harness-eval report /absolute/path/to/run
```

To publish the report online, follow [Hosting reports](../../docs/report-hosting.md).
