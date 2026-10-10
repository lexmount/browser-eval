# Inputs for access-restriction rule authoring

The starting delivery on 2026-09-27 contained 100 formal URL cases and 10 reserves. On 2026-09-29, insufficient current Kuaishou evidence prevented reliable rule authoring; the predeclared random procedure replaced `risk-107` with `risk-diverse-271`. The final 100 cases span 74 distinct sites. The nine unused reserves are outside the final scope and repository.

- `cohort/cohort-manifest.json`: URLs, site groups, target scope, language, broad qualification classes and one reference-text binding per formal case.
- `cohort/cohort-receipt.json`: qualification criteria, overall audit, the single replacement, external complete-audit hashes and limits.
- `cohort-policy.json`: cohort qualification and distribution constraints.
- `cohort/input-manifest.json`: input hashes and external evidence-root mappings. Relative paths resolve under `BROWSER_EVAL_DATA_HOME`, defaulting to `~/.browser-eval`.

Per-case rules use the compact manifest and independent page references. Original HTML, screenshots, HAR, DOM and detailed reviews proving historical restrictions remain in external storage, bound by the overall receipt. They do not enter Git or result scoring. Unused reserves remain in the original qualification workspace.

Earlier 50-case plans, obsolete 100-case rules and a historical 95/100 score are not authorities for this cohort. Check each reference's scope before authoring rules: hidden, collapsed or additionally loaded text is not automatically scored body content. Do not inspect evaluated-product output or call the products before rules are frozen.

Only the final 100 cases and necessary provenance are included here. Other candidates, eliminations, historical rules and scores remain in the original qualification workspace.

## Limits of qualification

The independent audit on 2026-09-28 inspected external original responses, DOM, network records and screenshots. Of the final 100 cases, 96 evidenced explicit security, anti-automation or access-policy restrictions; four evidenced only a login overlay. Qualification explains admission and does not score Moli output.

Evidence establishes restrictions for a specific URL, observation time and access route. It does not permanently classify a whole website or turn the four login overlays into bot detection. The scope is 100 URLs across 74 sites, not 100 sites.
