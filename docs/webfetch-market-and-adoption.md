# Web Fetch market and Agent adoption

Research date: 2026-08-31.

This document separates products that **provide** URL-to-content Web Fetch from
repositories that **use** Web Fetch. Pure browser sessions, sandboxes and search
APIs are adjacent products, not direct Web Fetch providers.

## Managed and self-hostable providers

| Provider | Form | Direct Web Fetch surface | Account requirement |
|---|---|---|---|
| Lexmount | Managed; this harness uses Global | `POST /v1/extract`, `POST /v1/dom/dump` | Global API key and project ID |
| Firecrawl | Managed and open source | `POST /v2/scrape`, crawl and structured extraction | Cloud key for production; self-hosting requires service dependencies |
| Tavily Extract | Managed | Extract API for known URLs | Tavily account and key |
| Exa Contents | Managed | Contents API for text, highlights and summary | Exa account/key or supported payment path |
| Cloudflare Browser Rendering | Managed | `/markdown` for browser-rendered URL-to-Markdown | Cloudflare account ID and scoped token |
| Browserbase Fetch / Extract | Managed | Current product pages advertise Fetch plus Markdown/Extract; REST reference may lag | Browserbase API key; verify account-visible schema |
| Bright Data Web Unlocker | Managed | Request API with unlocking, proxy and Markdown output | Token and Web Unlocker zone |
| ScrapingBee | Managed | Scrape API with JS/proxy/extraction options | Account/key |
| Zyte API | Managed | Extract API with automatic HTTP/browser selection | Account/key |
| ZenRows, ScraperAPI, Scrapfly | Managed | General scraping APIs | Individual account/key |
| Oxylabs Web Scraper API | Managed | Query API with rendering, parsing and geolocation | Account credentials |
| Crawl4AI | Open source | Python and deployable crawling service | No supplier account; browser/compute/proxy are operator costs |
| ScrapeGraphAI | Open source | Graph-based scraping and structured extraction | Model and runtime costs may apply |
| Browserless | Managed/open source | `/content` and browser REST APIs | Cloud token or self-hosting |

Not included: pure cloud browser/sandbox products, pure proxy networks, and
Apify without selecting a concrete crawler Actor. They may be part of a larger
browser harness but do not have one directly comparable Web Fetch contract.

Primary references:

- [Lexmount WebFetch](https://browser.lexmount.com/docs/webfetch)
- [Firecrawl Scrape](https://docs.firecrawl.dev/features/scrape)
- [Tavily Extract](https://docs.tavily.com/documentation/api-reference/endpoint/extract)
- [Exa Contents](https://exa.ai/docs/reference/contents-retrieval)
- [Cloudflare Browser Rendering `/markdown`](https://developers.cloudflare.com/browser-run/quick-actions/markdown-endpoint/)
- [Bright Data Web Unlocker](https://brightdata.com/pricing/web-unlocker)
- [Crawl4AI](https://github.com/unclecode/crawl4ai)
- [ScrapeGraphAI](https://github.com/ScrapeGraphAI/Scrapegraph-ai)

## Agent and framework adoption

A reproducible GitHub code-search pass used API/SDK endpoint signatures, then
verified repository stars and the default-branch HEAD commit through GitHub
GraphQL. The 2026-08-31 snapshot searches 32 endpoint names, SDK calls, generic
tool classes, search-plus-content tools and open-source extraction libraries
instead of relying on the literal phrase `web fetch`. It inspected 1,084 unique
repositories and retained all 136 candidates with at least 1,000 stars plus a
default-branch HEAD commit in the previous seven days, within the first 100 code
search results per signature. The complete query and file-level evidence is in
`research/webfetch-adopters-2026-08-31.json`; code-search hits are candidates,
not proof of runtime use.

Confirmed high-star runtime examples included:

| Repository | Integration observed |
|---|---|
| `openclaw/openclaw` | Built-in Web Fetch with optional Firecrawl path |
| `deepseek-ai/deepseek-harness` | Dedicated Web tool implementation |
| `n8n-io/n8n` | AI workflow Web Fetch tool |
| `Significant-Gravitas/AutoGPT` | Firecrawl scrape/search/extract blocks |
| `ollama/ollama` | Built-in Web Fetch tool |
| `langgenius/dify` | Firecrawl website extractor |
| `langflow-ai/langflow` | Tavily Extract component |
| `google-gemini/gemini-cli` | Built-in `WebFetchTool` |
| `infiniflow/ragflow` | Tavily extract integration |
| `lobehub/lobehub` | Exa crawler provider |
| `crewAIInc/crewAI` | Firecrawl and Bright Data tools |
| `run-llama/llama_index` | Bright Data, ZenRows and Oxylabs web readers |
| `janhq/jan` | Exa and Tavily providers |
| `AstrBotDevs/AstrBot` | Exa, Firecrawl and Tavily paths |
| `onyx-dot-app/onyx` | Firecrawl and Tavily open-URL implementations |
| `simstudioai/sim` | Firecrawl/Tavily/Exa/Bright Data tools |
| `activepieces/activepieces` | Firecrawl and Tavily actions |

The market does not converge on one provider. Built-in static fetching,
known-URL extraction, search-with-content, model-bound URL context and anti-bot
scraping APIs coexist. These are measurable contracts; “Agent-native” by itself
is not an evaluation category. Lexmount should fit existing provider interfaces
or MCP rather than introduce another model-visible synonym for `web_fetch`.

## Adjacent products that must use separate scorecards

| Product | What it actually provides | Decision |
|---|---|---|
| Browserbase Browser | Interactive browser/Stagehand extraction | Browser harness dataset, not lightweight fetch latency |
| Bocha Web Search | Query-to-ranked results, snippets and optional summary | Search dataset, not arbitrary URL dataset |
| Baidu Qianfan AI Search | Query-to-summary/URL/snippet results | Search dataset, not arbitrary URL dataset |
| Tencent Cloud Web Search API | Query-to-title/snippet/source URL results | Search dataset, not arbitrary URL dataset |
| Alibaba Model Studio | Model-bound `web_search` plus `web_extractor`; extractor currently accompanies search | Evaluate answer evidence, citation coverage, model tokens and tool cost |
| Google Gemini URL Context | Model retrieves supplied public URLs and returns an answer with URL-context status/citations | Evaluate model answer/evidence; it does not return a standalone raw Markdown artifact |

Google Search, Bing and Brave also provide search/discovery APIs rather than the
same known-URL extraction contract. A search provider can still feed its result
URLs into Lexmount; that pipeline should be measured as two stages so search
recall is not confused with extraction quality.

Browserbase is a versioning caveat: its REST reference still describes a raw,
no-JavaScript, 1 MB Fetch response, while newer official changelog, product and
pricing pages advertise Markdown/Extract. Treat it as a real similar supplier,
but verify the current account-visible request/response contract before adding
an adapter or score.

## Cost evidence

The real 10-URL run consumed 24 Firecrawl credits: nine ordinary cases reported
one credit each, while the 15-page PDF reported 15 credits. Firecrawl's public
pricing lists base scrape at one credit/page and PDF parsing with per-page
uplift, plus 1,000 free credits/month and a $5 one-time 1,000-credit option. The
observed run was $0 inside free allowance or roughly $0.12 at that listed unit
price. CowAgent has no supplier API bill but still has
operator compute/network/maintenance cost. Lexmount is documented as internal
testing and no public WebFetch price was found, so a cost-leadership claim would
be unsupported until Lexmount supplies a price card.

For not-yet-tested direct candidates, Tavily basic Extract charges one credit per
five successful URLs and grants 1,000 free credits/month; Exa's official x402
guide lists text contents at $0.001/page; Browserbase pricing lists raw Fetch at
$1/1,000 and Extract at $4/1,000 before proxy uplift. Pricing is date-sensitive
and must be snapshotted with every benchmark release.

Primary references:

- [Browserbase Fetch](https://docs.browserbase.com/platform/fetch/overview)
- [Baidu Qianfan AI Search](https://cloud.baidu.com/doc/qianfan-api/s/Wmbq4z7e5)
- [Tencent Cloud Web Search API](https://cloud.tencent.com/document/product/1806/121811)
- [Alibaba Model Studio web tools](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses)
- [Gemini URL Context](https://ai.google.dev/gemini-api/docs/url-context)

## CowAgent finding

CowAgent does not call an external Web Fetch supplier. Its implementation uses
repository-local HTTP requests, regex-based HTML stripping and optional document
parsers. It is a Web Fetch user and implementation, not a managed supplier
dependency.

The initial benchmark found that JavaScript content could return tool success
while expected page content was absent, and PDF dependency checks occurred after
download. The harness therefore separates transport success from content pass
and retains failed duration measurements.

## Initial Lexmount result

Official Lexmount documentation publishes separate Global (`.com`) and China
(`.cn`) API/console/credential domains. That establishes regional API surfaces;
it does **not** establish that the physical architecture consists of exactly two
service nodes. This harness uses Global only and does not add China as a second
benchmark provider.

On the ten-case public dataset, the global endpoint completed 30/30 requests
with P50 1.43 seconds and P95 1.87 seconds. Content markers passed in 80% of
attempts. Every response selected `lightmount_lite`; the JavaScript case returned
only a page shell and the PDF case returned an explanation rather than paper
text. This supports explicit Beta integration followed by engine-specific
JS/PDF evaluation, not immediate default routing.

## Credentialed three-way result (2026-08-31)

| Method | Transport | Content pass | P50 | P95 successful | P95 all attempts | Observed usage |
|---|---:|---:|---:|---:|---:|---:|
| CowAgent built-in | 9/10 | 8/10 | 1.87 s | 2.87 s | 39.43 s | local compute/network |
| Lexmount Global | 10/10 | 8/10 | 1.47 s | 2.91 s | 10 calls; public price unavailable |
| Firecrawl with key | 10/10 | 10/10 | 2.51 s | 7.19 s | 24 credits |

CowAgent's JavaScript page returned tool success but missed the rendered quote.
Its PDF was downloaded, then failed because `pypdf` was absent; the end-to-end
failure took 39.43 seconds. Lexmount also missed the JavaScript and PDF markers,
but failed at content validation rather than transport. Firecrawl passed both;
its PDF alone reported 15 credits, versus one credit for each ordinary page.

This single run is diagnostic, not a statistically stable latency verdict. The
framework supports repeated runs; the next publishable comparison should use at
least three repetitions, randomized provider order and a fresh/cache split.
