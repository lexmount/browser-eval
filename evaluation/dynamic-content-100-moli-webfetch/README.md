# Dynamic-content 100-case local audit

This directory contains the frozen, compact case definitions used by the
Moli 1.1.9 local audit. Measured provider output, debug logs, execution
manifests, and generated HTML stay outside Git under `BROWSER_EVAL_DATA_HOME`
or `~/.browser-eval/`.

The bound evidence can be rendered without making provider calls:

```bash
uv run --frozen python scripts/build_report.py \
  --local-audit \
  --evidence "$WEB_FETCH_EVIDENCE_DIR" \
  --output /tmp/dynamic-100-moli-119-report \
  --include-english
```

`--local-audit` requires explicit evidence and output paths and cannot be
combined with a publication profile. The resulting report is for local review
only and does not satisfy the formal release gates.
