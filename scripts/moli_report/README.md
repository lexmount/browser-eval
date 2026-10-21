# Read and reproduce a report

Serve `index.html` through a static HTTP server. `data/model.json` records the report configuration, task scope, execution counts and evidence index. Pass rates and resource metrics use their declared populations. Task pages expose acceptance rules, actual output, program errors and logs.

The public builder falls back to English. Set `BROWSER_EVAL_REPORT_LANGUAGE=zh` or pass `--language zh` for a Chinese-only build. `report-languages.json` records generated languages; an absent English edition shows a notice and return link. Bilingual builds share the same results and evidence.

Functional results and resource measurements bind their own build identities. Check binary hashes and experiment conditions before using the results; compare platform-specific memory measures separately.

`reproduction/run.py` uses Browser Eval's shared library. Run `uv sync --frozen` in the source checkout, then use that environment to run the downloaded script. `--help` lists required arguments. The script defaults to adjacent `cohort.json` and `protocol.json`, which can also be supplied explicitly.

Prepare the frozen protocol's container images, browser binaries, driver inputs and dependencies separately. The script verifies input hashes and prints commands; only `--execute` runs them. Use a new output directory for each execution and retain original results and certificates.
