# Frozen WebMainBench-545 comparison

This is a separate frozen-HTML-to-Markdown fidelity study, not the live-URL
task benchmark described in [HEADLESS.md](HEADLESS.md). Its current authority is
`evaluation/current-report.json`: 200 pages selected from the public 545-page
parent before any method result, five browser implementations, one logical
result per method and page.

The source JSONL is bound by SHA-256
`0efaa4b49a45e320a27fe6e5a0b6aad5b57259fc3321ac3448519cacc74c537e`.
Every method receives the official `html` field encoded as UTF-8 without
editing its bytes. Lightpanda, Obscura, Moli and Chromium load it from a
loopback HTTPS fixture under a macOS sandbox that permits only loopback
networking; each batch has a dedicated temporary CA. Kitesurf uses
Cloudflare Browser Rendering's documented inline-`html` input and rejects
external resource types. Original URLs are retained only as source metadata.
Inline scripts included in the frozen bytes may run, but external subresource
and data requests cannot reach the network. The dataset's public `cc-select`
annotation remains an inherent cue, although none of the five general
converters is customized to consume it.

| Method | Measured output route |
|---|---|
| Kitesurf | Cloudflare Browser Rendering `/markdown?browser=kitesurf` |
| Lightpanda | Native `fetch --dump markdown` |
| Obscura | Native `fetch --dump markdown` with private-loopback allowance |
| Moli 1.1.5 | Native `fetch --dump markdown` |
| Chromium 152 | Rendered DOM converted by `node-html-markdown` 2.0.0 |

The pinned official scorer commit is
`9d991bdc00c57b57521499494d96be85c31317ba`, using its deterministic
`use_llm=false` configuration. The five content components are `text_edit`,
`code_edit`, `formula_edit`, `table_edit` and `table_TEDS`; official `overall`
is the arithmetic mean of their separately aggregated scores. Every component
uses only pages successfully scored for that component, so denominators can
differ. The per-page `overall` has a different denominator and must not be
substituted for the leaderboard `overall`. Request delivery and end-to-end
latency, including declared failed retries, are separate measures.

Run the repository's `make public-benchmark-sample`, `webmainbench-run`,
`webmainbench-score`, `webmainbench-review`, `webmainbench-assemble` and
`public-benchmark-report` targets with their required absolute-path arguments.
The build reads persisted, independently reviewed evidence outside Git. The
report exposes the 200-page sample, official human Markdown references,
frozen HTML, five same-page results, per-component scorer reasons, full final
outputs and physical retry chains. A failed retry keeps every physical call
and uses the final response, not the best response. A corrected contaminated
batch supersedes its whole declared scope while retaining the prior evidence.

The results apply to these 200 frozen pages and execution routes. They are
not the paper's 7,809-page ROUGE-5 ranking, the 545-page parent aggregate,
live-Web Fetch success rates, or an isolated CPU/memory/network comparison.
