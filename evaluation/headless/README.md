# Complete calls, publication still blocked

All five browsers ran all 150 repository tasks. `results.jsonl` contains exactly
750 selected calls. Three five-provider cases were re-executed after a
certificate-assembly failure; the original 15 calls, outputs and failed checks
remain in `diagnostics/replaced-cases/`. Selection was by failed certificate,
not by score. Task rules were not changed.

99 of 100 ordinary-page references have verified live pre/post certificates.
`web-retail-returns` has an independently observed precheck but its postcheck
reached a human-verification page. That is a reference limitation, not a browser
failure. Its raw output, task-rule diagnostics and failed reference certificate
are retained. The other 50 tasks use their existing scoped contracts/evidence.

The cohort is explicitly `blocked_reference_checks`. The normal report builder
rejects it; no guard is bypassed, no historical call is certified retroactively,
and no final report has been published. A fresh complete five-browser cohort is
needed for the blocked case once independent reference access is restored.

Audit without network calls:

```sh
python scripts/check_headless_evidence.py --rescore
```

The unpublished, scoped summary is
[`research/headless-diagnostic-summary.md`](../../research/headless-diagnostic-summary.md).
The full protocol and runtime versions are in
[`harness/web_fetch/HEADLESS.md`](../../harness/web_fetch/HEADLESS.md).
