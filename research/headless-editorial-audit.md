# Headless comparison: private editorial and measurement audit

This file is not a report page and must not be copied to the publication bundle.
The public article contains only the formal method, evidence and result changes.

## Evidence status

- Canonical bundle: 150 tasks × five methods = 750 unique selected calls.
- Offline verification reproduces every persisted score; all available exact-body
  pre/post certificates, hashes, task rules and call identities pass.
- Two live full-text references are explicitly unavailable. Their stable fact
  checks remain active while word precision/recall/F1 remain unmeasured.
- Final ordinary-web passes: Chromium 98, Kitesurf 85, Lightpanda 91, Moli 93,
  Obscura 91. No cross-family total score is published.
- Final dynamic passes: Chromium 8, Kitesurf 7, Lightpanda 5, Moli 7, Obscura 7.

## Method decision

Kitesurf uses the Cloudflare account Browser Run `/markdown` endpoint. Lightpanda,
Obscura and Moli use their local native Markdown CLI output. Chromium/CDP has no
native Markdown output; Reader Mode emits selected HTML and does not satisfy the
full task scope. The measured Chromium route is Playwright 1.58 + Chromium 152,
full rendered DOM, then node-html-markdown 2.0.0.

The converter selection used the same retained Chromium DOM cohort. Web task
passes were node-html-markdown 98/100, pyhtml2md 97/100, Xberg 92/100, Turndown
58/100 and Defuddle 47/100. Node conversion across 148 DOMs was about 18.2 s in
total versus about 139.9 s for pyhtml2md. The final Chromium figures come from a
fresh full rerun with the selected converter, not from the offline probe.

## Fairness ledger

| Claim | Evidence | Limit |
|---|---|---|
| Compare delivery before speed | Shared URL, task and `score_attempt`; per-family pass counts | Not a general browser-engine ranking |
| Pair latency on identical successful URLs | `headless_evidence().pairs[].both_pass_ids` | Different pair rows can still have different URL sets |
| Preserve fastest product route | Four native Markdown paths; Chromium fallback only | Internals and readiness conditions intentionally differ |
| Treat Kitesurf as remote | Account API metadata and `browser_ms_used` retained | Client elapsed includes network/queue; not same-hardware engine time |
| Keep task quality separate | Task pass plus word precision/recall/F1 | Overlap is not semantic or freshness verification |
| Keep file contracts separate | No added parser/OCR/downloader in this cohort | Failures do not prove the product lacks optional capabilities |

## Result audit

Every old selected call was checked against its stored output, reference and
per-check decision, not only the failed rows. The focused rule audit corrected
43 false negatives and one false positive. The highlighted
`run-f4e6f2ee641e8baa9e3d` is a false negative: all three Feishu facts exist and
reference recall is 1.0; the old visible-text rule broke a sentence across a
multiline Markdown link. The one false positive was Kitesurf's incomplete
RAGFlow README response. The final additional false negative was Moli's complete
Swagger catalog: its native `METHOD[/path](target)Summary` layout had removed the
field gaps during visible-link normalization; the provider output itself was complete.

After main was synchronized, seven old passes were independently confirmed as
failures due to missing table headers, absent README regions, field
concatenation or a partial error page. The changed Django reference was
independently frozen and rerun for all five methods; Moli returned browser_exit.
Other physical batches remain selected only when reference bytes and evaluation
scope are identical, while retaining their own strict capture binding.

## Editorial and visual pass

The article states the result first, then explains method routes, Kitesurf's
current non-open-source status, matched latency, dynamic delivery and the audit.
Promotional claims, a universal winner, invented engine internals and user work
logs are excluded. Desktop 1600×1200 and mobile 390×1100 screenshots confirm
readable hierarchy; the mobile task ID is visible above the fold and wide tables
remain within their scroll container. Automated validation covers 916 HTML
pages, zero broken local links, report filters/sorting, packaged navigation and
all 750 result records.
