# Dynamic-content 100-case local audit

This directory contains compact case definitions derived from the frozen
Moli 1.1.9 local audit. The historical dataset and scoring bindings remain in
`research/dynamic-100-moli-119-local-audit.json`; subsequent source-language
corrections are separately bound by
`research/moli-111-dynamic-document-language-revision.json` and
`research/moli-111-undeclared-source-language-revision.json`. They do not
rewrite the historical measured results.

`target_language` describes the requested content locale.
`expected_document_language` describes an independently observed HTML language
tag when it differs. `expected_document_language_present: false` means the
source document omits that tag; identity verification then requires an explicit
empty observed language value and the expected final URL. An unavailable value
does not satisfy this rule.

Measured provider output, debug logs, execution
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
