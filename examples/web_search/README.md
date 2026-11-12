# Web Search report example, version 1

Generate a deterministic, explicitly synthetic two-method report with a successful response and an upstream failure:

```sh
uv run --frozen python -m harness.web_search.example --output /tmp/search-example
uv run --frozen python -m harness.web_search report --output /tmp/search-example/report --check
```

For the two-benchmark comparison layout, including complete synthetic relevance
grades and both language editions:

```sh
uv run --frozen python -m harness.web_search.example --output /tmp/search-comparison-example --comparison
uv run --frozen python -m harness.web_search report --output /tmp/search-comparison-example/report --check
```

Open `/tmp/search-example/report/index.html` through a local HTTP server to use the on-demand evidence dialogs. The renderer and fixture are owned by `src/harness/web_search/`; this directory carries no measurements. See the [track guide](../../src/harness/web_search/README.md) for authenticated collection and metric definitions.
