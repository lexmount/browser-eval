---
name: benchmark-editorial
description: Write or substantially revise a standalone, publication-ready Chinese technical article interpreting this project's benchmark evidence; also prepare its HTML reading and export formats. Use for report interpretation, official blog or public-account articles, not routine result-table rebuilds or release notes.
---

# Benchmark editorial

Deliver an article that a reader can understand without opening the benchmark dashboard. It must help a technical or technically curious reader explain a result and make a decision. A tour of report sections, a metrics glossary, a compressed report summary, and a transcript of the author's work are different deliverables.

Keep this skill and its dependencies inside this repository. Read [references/editorial-standard.md](references/editorial-standard.md) before substantial writing, [references/html-delivery.md](references/html-delivery.md) before HTML work, and the sibling [Humanizer](../humanizer/SKILL.md) before the final prose pass. Humanizer runs in embedded/file mode: only the finished article belongs in public artifacts. These requirements do not authorize publishing, spending on provider calls, changing scores or reading credentials outside the repo.

## Workflow

1. **Establish the evidence.** Read root AGENTS.md, the canonical dataset, persisted outputs, runtime configuration and current scoring code. Build the current report so API-success normalization matches the dashboard. Identify the measured unit, denominators, task types, dates and incomparable groups. Keep observed results, mechanically derived results, mechanism experiments and hypotheses distinct. Do not start with the old article's outline.
2. **Find the argument.** Study the user's references in full. Choose an important phenomenon supported by this run: a rank reversal, a failure concentration, a simpler method working well, or a change that did not improve the outcome. Form a claim, ask what alternative explanation could produce it, and inspect representative and counterexample records. Explore at least one decision-relevant question beyond existing tables when the data supports it (e.g. failure overlap or matched-task latency). Do not force this run's thesis onto future runs.
3. **Build a private claim ledger.** Record the question, source paths/IDs, calculation, interpretation and limit for the claims that carry the article. Store the editorial audit outside `reports/`; never publish assistance requests, discarded drafts, prompt instructions or reviewer chatter. Missing evidence limits the claim; it is not permission to invent data.
4. **Write the article.** Start with a concrete finding or problem and explain enough context to make it matter. Follow the reader's questions, not the database schema. Definitions belong at the point of use. A reader should get the task, observed difference, plausible mechanism, counterexample and practical consequence in one reading. Charts should carry exact comparisons while prose does the reasoning. There is no required chapter count, FAQ, TL;DR, hero image or word quota.
5. **Edit twice.** First check factual scope and denominators against code and artifacts. Then run Humanizer's draft → audit → final loop, using the user's Chinese examples for register and punctuation. Remove drafting announcements, repeated negations, inflated claims and formulaic endings. Do not add invented personal experience or trade factual caution for confidence. Recheck every number and claim after editing.
6. **Deliver and validate.** Use the current `harness/web_fetch/report_explainer.py` and its authored template; do not introduce a versioned renderer. Generate a dashboard-integrated reading page and a self-contained HTML article for redistribution from the same text. Test data-derived figures and evidence links. Check desktop, narrow-screen and print styles. When publication is authorized, use the existing Artifact Hub site, verify the published bytes and test the deployed article.

## Stop conditions and handoff

Do not certify literary quality from a passing regex or word-count test. Complete a substantive editorial review with explicit evidence of fixes. A missing service credential blocks publication, not local writing. A disputed score requires a separate scoring investigation; prose editing must not silently change it. For sign-off, provide the article link/file, the reusable skill location, and material evidence limitations. Do not paste the entire article into the conversation unless asked.
