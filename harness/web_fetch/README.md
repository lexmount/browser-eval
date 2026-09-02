# Web Fetch Harness

Web Fetch providers, execution, reference checks, scoring and report generation
live in this package. Browser Use remains independent in `harness/browser_use`.

From the repository root:

```sh
python -m harness.web_fetch validate --providers fixture
python -m harness.web_fetch run --providers moli-webfetch --case-ids static-simple
python scripts/build_report.py
```

Credentials and local executable/repository paths belong in the root `.env`
(copy `.env.example` and fill only the methods you use). Exported environment
variables take precedence; `--env-file` overrides which file is read.

`moli-webfetch` invokes the official local `moli fetch --dump markdown` CLI;
set `MOLI_BINARY` when it is not on PATH. It needs no API key. No alternate
provider or output-format fallback is used after a failed call. Screenshots
and PDF export are official Moli capabilities, not enabled by this text adapter.

The installed console command is `browser-harness-eval`. There is no legacy
Python package or compatibility entrypoint.
