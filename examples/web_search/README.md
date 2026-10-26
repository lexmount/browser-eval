# Web Search report example, version 1

Generate a deterministic, explicitly synthetic two-method report with a successful response and an upstream failure:

```sh
uv run --frozen python -m harness.web_search.example --output /tmp/search-example
uv run --frozen python scripts/build_search_report.py --output /tmp/search-example/report --check
```

Open `/tmp/search-example/report/index.html` through a local HTTP server to use the on-demand evidence dialogs. The renderer and fixture are owned by `harness/web_search/`; this directory carries no measurements. See the [track guide](../../harness/web_search/README.md) for authenticated collection and metric definitions.
