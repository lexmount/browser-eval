# Ground-truth review log

Dataset: `public-webfetch-v2.1`

Review date: 2026-08-31

Annotator: Codex

The answer key was frozen before the v2 supplier run. Facts were checked against
the source page itself, not inferred from a participating supplier's output.
Each case page in the HTML report links to its source evidence and shows every
accepted alternative.

Verification methods:

- ordinary HTML and redirects: source page text and destination content;
- JavaScript case: rendered DOM from local headless Chromium;
- PDF case: the 15-page arXiv PDF text, including title, author, Transformer and
  Scaled Dot-Product Attention;
- product documentation: the published CowAgent and Lexmount documentation;
- GitHub: the current default-branch README rendered by GitHub.

The annotation deliberately excludes dynamic values such as stars, article
revision dates and latency. Facts are stable semantic units, not an exhaustive
copy of each page. A content pass requires all registered facts, a minimum
length and no registered error/challenge marker.

This is a verified single-annotator dataset, not an infallibility claim. Before
using the benchmark in external marketing or procurement, a second reviewer
should independently accept/reject every fact group. Any change to the answer
key requires a new dataset version and a full rerun.

## v2.1 correction log

The first strict score exposed three annotation false negatives. They were
checked against source pages before final scoring: Example Domain now displays
`Learn more`; Books to Scrape visibly truncates the first title to
`A Light in the ...`; and GitHub may include a local loading-error template even
when the complete README is present. v2.1 adds the two source-visible
alternatives and removes that GitHub template string as a global hard failure.
Raw supplier artifacts were not modified; the final metrics were recomputed
from their hashes.
