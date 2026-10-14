# Authoring and checking access-restriction task rules

This guide describes rule authoring for the formal 100-URL cohort. It is methodology, not a completed per-case answer key or evidence of product execution. Existing frozen rule and audit artifacts retain their original language and hashes.

## 1. Scope, evidence and order

### Formal population

Use the [cohort manifest](qualification/cohort/cohort-manifest.json) and [input manifest](qualification/cohort/input-manifest.json). The 100 cases cover 74 sites. `risk-diverse-271` replaced `risk-107` under the random-selection record in the [cohort receipt](qualification/cohort/cohort-receipt.json). The nine unused reserves receive no rules and are outside Git. Never select replacements according to Moli's ability to fetch them.

### Authoring boundary

Read independent page evidence, determine scope, write rules, validate them and freeze the result. Do not call evaluated products, generate scores or publish reports during authoring. Reuse qualification conclusions; investigate only missing, contradictory, changed or insufficient evidence. Saved body text is not automatically a complete acceptance contract.

Do not inspect product output first and then choose convenient phrases, remove fields, lower counts or shorten references. Historical scores for the five overlapping cases are not rule evidence.

### Per-case sequence

1. Check the case ID, input URL, language, target scope and evidence location.
2. Read saved text, original pages, scope annotations and relevant screenshots or structure.
3. Check redirects, target identity and language; revisit the original target when needed.
4. Define required and excluded content before choosing rule types.
5. Record required stable text, links, fields, counts and body coverage with their evidence.
6. Test complete content and deliberately missing or mismatched content for both error directions.
7. Save rules, reference scope and hashes with a frozen or unresolved status.

## 2. Eligible page content

### Reuse and targeted capture

Prefer saved HTML, text, redirects, structure and screenshots. Revisit only when evidence is absent, contradictory, ambiguous, stale or insufficient. Read each reference's scope alongside its bytes: navigation, ads, hidden paragraphs and challenges may appear in extracted text without belonging to the target body.

Judge stability from content type, position and observations. Several unchanged refreshes do not establish permanence. Historical observations establish past states only.

Inspect the complete initial automatic-load scope. Category navigation, sidebars, controls, fixed footers and ordinary text buttons are candidate stable text when independently shown to exist without clicking, navigating, typing or scroll-triggered loading. Assess each region rather than excluding it merely because it is outside the body. Do not freeze login state, selected values, experimental labels, personalized entries or other state-dependent text.

### Beyond the viewport

Already loaded normal content below the viewport can qualify. Screenshots help establish layout, overlays, collapse and error states; use full text, HTML or structure to verify content and links. A screenshot alone cannot define a whole-page contract, and extracted text alone does not establish visibility.

Initially loaded iframe/subdocument content can qualify with declared ownership. A parent title, embed tag or empty frame does not satisfy the subdocument target.

### Exclusions

Do not use OCR, text within images/posters/icons/image buttons, alt text, hidden templates, scripts or still-collapsed content. Exclude content requiring clicks, expansion, typing, pagination or scroll-triggered loading. Challenges, access-denied pages, login notices and loading placeholders are not target body text. A browser-tab title absent from visible page content is not visible stable text.

Ordinary text buttons can qualify without being clicked when visible, stable and relevant. Their acceptance role follows the content contract: diagnostic when body or composite requirements exist, mandatory otherwise. They cannot replace body or list completeness.

### No interaction during supplementary capture

Open only the fixed target URL and wait for initial automatic load. Do not click, type, search, expand, paginate, log in, accept consent walls, enter detail pages or scroll. Verify below-viewport content through saved text and structure. Historical capture permissions do not automatically permit repeating those operations now.

Separate completion from acceptance. Record the observed completion condition and wait limit; completion does not prove complete content. Derive waits from independent observations, not repeated extensions based on product failures.

## 3. Fix the URL, target and language

For each case declare one formal URL shared by reference capture and Fetch, the target object, target language under the cohort's Chinese/English agreement, observed redirects/final URL and necessary locale settings. Two pages on one site are not automatically the same target.

Investigate real language paths or preferences when redirects change the language or target. Use verified `/en/`, `/zh-cn/` paths or supported parameters; never guess language suffixes. Confirm validity, original target and body language after changes. Browser translation does not establish the source language.

Record ordinary HTTPS/canonical redirects. If adopting the final canonical URL as a new input, verify that restriction and body evidence applies to that input. Preserve old/proposed URLs, reasons and checks; changes to target or scope remain unresolved rather than silently replacing a case. Unfixed language or identity cannot be masked by accepting both languages.

Deliver the verified URL and necessary settings for later calls. This describes observed applicability, not permanent website behavior or a Moli call that has not happened.

## 4. Rule combinations

A case may combine several rule types. Every selected requirement must pass; success in one cannot compensate for another. Not every page needs every type.

| Rule | Question |
| --- | --- |
| Target identity | Is this the declared target and language, rather than an error or shell? |
| Stable text | Are selected normally stable phrases present? |
| Name-link | Does each name have a real compliant link owned by that record? |
| Same-record fields | Are required real fields present without borrowing across records? |
| Count and deduplication | Are enough valid distinct records present relative to independent observation? |
| Body coverage | Is the declared body covered beyond its title or opening? |

Each rule must bind actual page evidence, not just a type label.

## 5. Target identity

Use independently supported article titles/topics, product names/models, or section names and actual list structure. A generic site name rarely distinguishes pages on one site. Wrong articles/products/languages, navigation-only pages, challenges, login-only pages and loading shells must not pass just because the site name matches.

Do not reject solely because words such as “login” or “CAPTCHA” occur. An article may discuss them, and genuine target content can coexist with a normal login link. A correct article with that link may pass; a correct-looking URL returning only a site name and browser-check notice must fail.

## 6. Stable text

### Selection

Choose phrases expected to remain normally present: established sections, fixed explanations, headings, stable body content and suitable navigation, footers or text buttons. Record content type, page position, evidence and the basis for stability. Being observed once is insufficient; permanence is not required.

Do not freeze current leaderboard members, news headlines, prices, ranks, stock, popularity, counters or copyright years. For copyright text retain a meaningful stable phrase without a changing year. An independently supported fixed article title or product identity differs from a current list member.

### Groups and acceptance

There is no universal minimum or maximum group count. Do not stop at three or add unsuitable phrases to reach three. Overlapping fragments of one sentence are not independent identity items. Each group needs a meaningful phrase, original text, position, purpose and stability evidence; avoid large spans containing volatile dates or prices.

Stable text is `required` by default. A body or list-contract owner may explicitly set `stable_text_role` to `diagnostic` only after its mandatory body regions, structured records and any `page_patterns` together cover the complete qualified target scope. Missing diagnostic groups remain visible in coverage and per-group results but do not reject complete target content. Select peripheral text carefully; navigation and footer matches cannot establish body, identity or list completeness.

### Formatting and variants

Use shared matching for reasonable case, whitespace, line-break and Markdown differences. Never delete meaningful numbers, words or symbols to improve matches.

Default to one independently observed phrase. Add alternatives only when independent evidence establishes necessary wording variation for the same target and language. For example, observed “About this project” and “About the project” may share one group when identity is unchanged. Bind evidence and explain why both are needed.

Do not invent synonyms, add variants from Moli output or use Chinese/English alternatives to hide language drift. Frequently changing text should usually cease to be a mandatory stable phrase rather than gain unbounded accepted forms.

## 7. Name-link records

### Ownership and real links

Article, product, video and repository lists require a nonempty actual visible name and an actual link belonging to the same record. Generic “click here”/“details” text is usually not a specific name. A card title and another link in that card may be paired when ownership is proven; do not borrow across records.

Never manufacture URLs from IDs, names, path patterns or experience, guess suffixes or add links to unlinked cards. IDs identify existing links and duplicates; they do not create links.

### URL contracts

Use independent actual links to declare allowed schemes, exact hosts, full path structure, necessary parameters and endings. A domain substring or ID substring is insufficient. Illustrative fictional patterns include `https://shop.example/product/12345` and `/item?id=12345`; only real case evidence determines which pattern, ID alphabet/length and trailing slash is valid.

Parse hosts so `shop.example.evil.test` and `evil.test/?next=shop.example` cannot imitate `shop.example`. Allow only verified subdomains. Reject `javascript:`, empty links and placeholder anchors. Specify whether paths such as `/product/12345/reviews` represent the declared detail target; a prefix match alone is insufficient. Correct syntax also needs actual page provenance, name ownership and target-region membership.

### Relative links and parameters

Resolve real relative links against the true page/base URL. An ID without a link cannot be completed this way. Retain identity, language and region parameters. Ignore tracking only when evidence proves it does not change the target; do not discard all queries or fragments, since some select content. Retain original and comparison/deduplication forms.

### Changing membership and counts

Freeze structure, ownership, allowed URL forms, deduplication and count threshold, not historical member names or URLs. Name-link rules also require valid distinct returned records to reach at least `ceil(independently observed valid count × 0.80)`, subject to independently justified fixed-count exceptions.

For 20 independently observed records, require 16. Returning 18 with four duplicates or invalid links counts as 14 and fails. This is neither “80% of returned items are valid” nor a requirement to return the same historical 16 members. Missing names, links or fields do not count; navigation, help, categories, ads and recommendations cannot impersonate the target list. Tracking variants of one target count once.

## 8. Same-record fields

Without a normal detail link, do not impose or manufacture one. Select actual required textual fields in cards/tables; combine with name-link when real links belong to the target requirement. Specify required, valid and optional fields. Product name/price, match teams/status or earthquake location/magnitude are examples; fields available only after entering a detail page are outside initial scope.

Values may change: validate price/date information rather than freezing today's amount/day. Zero is not automatically missing. “Unavailable” and “loading” require field-specific interpretation rather than automatic acceptance. Required fields must belong to one record. Conditional applicability comes from declared page facts, not exemptions introduced after product omissions.

Empty cards, skeletons, repeated buttons and counts of images are not valid records. Valid distinct records also satisfy the count requirements below.

## 9. Counts and deduplication

### Population and boundary

- `N`: independently observed valid distinct records in the same language, target region and initial automatic-load scope.
- `T = ceil(N × 0.80)`: minimum count.
- `M`: returned records satisfying names, links, fields, region and deduplication.
- Pass: `M ≥ T` and every other required rule passes.

For `N=12`, `T=10`; for `N=20`, `T=16`. Never use Fetch's own count or a site's total inventory as `N`. Records loaded only after interaction do not enter `N`.

### Fixed counts, variation and changes

A full fixed count may be required when page design and independent evidence support it. Changing lists default to 80%; any exception needs independent per-case justification, never weaker limits based on product output. Investigate fluctuating initial counts for target, language or load-scope changes. Preserve observations and the final basis; do not choose the lowest count to hide incomplete loading.

`N=0` or unknown valid count does not complete a rule with a zero threshold. Determine whether the page is wrong, incomplete or lacks required targets, then record missing evidence or replacement needs. Freeze thresholds rather than adapting to each response. Preserve evidence and revise the affected scope when the real page changes.

### Identity and regions

Prefer content identity in real detail links; unlinked records use justified field combinations. Do not deduplicate by title alone because distinct content can share titles. Tracking links and repeated appearances across header/list/recommendations cannot inflate counts. Declare separate regions and minima for multiple required lists; excess in one cannot compensate for another's absence.

## 10. Body coverage

### Reference scope

For articles, documentation, product explanations, posts and fixed text, define the original task scope before capture. Include complete independently verified target body already visible or loaded after initial automatic load, including below-viewport content needing no expansion or additional loading. Divide the complete scope, not selected opening paragraphs or convenient chapters, into at most five contiguous regions.

Volatile target news, lists, versions or prices cannot simply disappear from acceptance: represent them with name-link, same-record fields and count rules instead. Exclude navigation, ads, recommendations, footers, login notices and unrelated text from body coverage even when independently required as stable phrases. Remove capture duplication without deleting real body content to accommodate outputs.

If only some chapters are verified, declare that scope. A broader original task needs more evidence; partial coverage cannot become “full text.” Very short pages need meaningful rules rather than padded references.

### Regions and thresholds

Prefer natural paragraphs, chapters or complete components. Up to five contiguous regions cover beginning, middle and end; short text may use one. Automatic equal splitting is a tool, not a substitute for checking boundaries.

Every region needs at least 60% coverage, and at least `max(1, region_count−1)` regions need 80%. A single region needs 80%.

| Regions | Requirement |
| --- | --- |
| 1 | At least 80% |
| 2 | Both at least 60%; at least one at 80% |
| 3 | All at least 60%; at least two at 80% |
| 5 | All at least 60%; at least four at 80% |

Three regions at 90%, 85%, 65% pass; 95%, 95%, 20% fail. An 80% whole-text average or one strong region is insufficient. Any threshold exception needs independent evidence before observing product output.

### Meaning of coverage

Use the shared body scorer. Version 3 normalizes some formatting, retains letters/digits and compares overlapping six-character fragments against reference regions. This is textual fragment coverage, not semantic equivalence, sentence counting or character-count proportion. Shared fragment occurrences are allocated across regions; one returned occurrence cannot supply unlimited matches. Test missing sections and repeated fragments; the metric does not establish full authenticity or order.

If a region cannot yield meaningful fragments, merge appropriate adjacent text or use required short-text phrases with justification; do not introduce empty rules that pass automatically. Do not freeze changing home-page recommendations or current news summaries as permanent body text. Fixed article/post text can qualify; frequently changing targets may need record/field rules.

## 11. Example combinations

These are design examples, not frozen answers for actual cases.

| Page | Typical requirements | False passes to prevent |
| --- | --- | --- |
| Fixed article | Identity/language, stable phrases, body regions | Title and opening only |
| Changing leaderboard | Section/language, stable text, name-link, deduplication, 80% count | Old members, invalid links or repeats |
| Unlinked cards | Identity, stable text, same-record fields, count | Skeletons or fields borrowed across records |
| Product detail | Product identity, stable explanations, fields, relevant body | Another product or an old price frozen as truth |
| Short post | Post/author identity, suitable phrases and short-body check | Arbitrary phrase quotas or replies used as post text |
| iframe page | Parent identity and actual subdocument target | Parent title or embed tag only |

Every declared body region and composite group applies. Stable-text groups are diagnostics when either contract exists; cases with neither retain mandatory stable text. Complete footers cannot replace body content, and sufficient links cannot replace a declared body region.

## 12. Validate both error directions

Exercise the shared scoring entry point, not only visual inspection of configuration. The guide itself does not execute these checks. Complete independent references should pass, including reasonable spaces, line breaks, case, Markdown tables/lists, real relative-link resolution and verified irrelevant tracking differences.

Include positive controls for complete body or records without peripheral phrases. Construct applicable negative controls for missing mandatory stable phrases in anchor-only cases; site/navigation/footer-only outputs; wrong target/language; challenge-only or loading-shell responses; empty names; ownership mismatch; wrong hosts/paths; incomplete or invented links; IDs without links; missing or borrowed fields; duplicate records; wrong-region records; counts one below threshold; missing middle/end; repeated openings; and capture duplicates creating false coverage.

Normal articles discussing login or CAPTCHA must not fail solely for those words. Exact threshold boundaries use unrounded values; display rounding cannot change verdicts. URL syntax alone cannot establish every new member's authenticity: require the declared provenance and ownership checks.

If the shared scorer cannot express an independently established rule, record and fix the common gap with positive and negative controls. Do not silently delete requirements, claim verification or patch one product output.

## 13. Per-case deliverables

Record ID, page name, formal URL, final URL/redirects, language/settings; original target, initial-load scope, completion and exclusions; independent evidence locations, observation times/hashes and reasons for reuse/capture; stable phrases with positions, purpose, stability and justified variants; actual link sources, URL/parameter contracts, ownership and deduplication; fields, valid values, `N`, `T` and exceptions; complete body reference, regions, scorer version and thresholds; positive/negative results, limits and frozen/missing-evidence/unresolved/replacement status.

Keep original pages and observations in external evidence storage. Do not copy product outputs, credentials or internal execution logs into Git. Version rules and compact evidence references, retain prior frozen provenance and record revision effects rather than overwrite historical conclusions silently.

## 14. Sources and applicability

The final cohort inventory, original evidence, per-case scope and mappings to historical material are bound by paths and hashes. Unused reserves stay outside Git. Dynamic-task methodology supplies independent evidence, rule combinations, link/field/count/body techniques and bidirectional checks, but not its dataset-specific counts or qualification criteria.

Consult the [shared body implementation](../../harness/web_fetch/body_coverage.py) and [format handling](../../harness/web_fetch/metrics.py) for actual metric semantics. This cohort does not inherit an older three-phrase minimum, a mandatory extra visit per case or old candidate counts. It uses only the formal 100 cases, predeclared random reserve selection when needed, page-specific stable text without quotas, necessary evidenced variants, explicit body/record acceptance with stable-text diagnostics, anchor-only fallback, evidence reuse, real links and at least 80% valid name-link count.
