# Web Fetch market and Agent adoption

Research date: 2026-08-31.

This document separates products that **provide** URL-to-content Web Fetch from
repositories that **use** Web Fetch. Pure browser sessions, sandboxes and search
APIs are adjacent products, not direct Web Fetch providers.

## Managed and self-hostable providers

| Provider | Form | Direct Web Fetch surface | Account requirement |
|---|---|---|---|
| Lexmount | Managed, China/global regions | `POST /v1/extract`, `POST /v1/dom/dump` | API key and project ID for the matching region |
| Firecrawl | Managed and open source | `POST /v2/scrape`, crawl and structured extraction | Cloud key for production; self-hosting requires service dependencies |
| Jina Reader | Managed | `GET https://r.jina.ai/{url}` | Key optional at low rate |
| Tavily Extract | Managed | Extract API for known URLs | Tavily account and key |
| Exa Contents | Managed | Contents API for text, highlights and summary | Exa account/key or supported payment path |
| Browserbase Fetch | Managed | Fetch and Extract APIs alongside browser sessions | Browserbase account/key |
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
- [Jina Reader](https://jina.ai/reader/)
- [Tavily Extract](https://docs.tavily.com/documentation/api-reference/endpoint/extract)
- [Exa Contents](https://exa.ai/docs/reference/contents-retrieval)
- [Browserbase pricing](https://www.browserbase.com/pricing)
- [Bright Data Web Unlocker](https://brightdata.com/pricing/web-unlocker)
- [Crawl4AI](https://github.com/unclecode/crawl4ai)
- [ScrapeGraphAI](https://github.com/ScrapeGraphAI/Scrapegraph-ai)

## Agent and framework adoption

A reproducible GitHub code-search pass used API/SDK endpoint signatures, then
verified repository stars and the default-branch HEAD commit through GitHub
GraphQL. Within 17 explicit signatures and the first 100 results per signature,
575 unique repositories were inspected and 94 candidates had at least 1,000
stars plus a default-branch commit in the previous seven days. Documentation and
secret-detection rules can still be false positives, so runtime use was reviewed
separately.

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
| `simstudioai/sim` | Firecrawl/Jina/Tavily/Exa/Bright Data tools |
| `activepieces/activepieces` | Firecrawl and Tavily actions |

The market does not converge on one provider. Built-in static fetching,
Agent-native extraction APIs and anti-bot scraping APIs coexist. Lexmount should
fit existing provider interfaces or MCP rather than introduce another
model-visible synonym for `web_fetch`.

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

The evaluated project credential was regional:

- `LEXMOUNT_API_KEY` with `https://api.lexmount.com` succeeded;
- the same key with the China endpoint returned 401;
- the supplied `LEXMOUNT_WEB_FETCH_KEY` returned 401 on both endpoints.

This is a credential-binding result, not an endpoint performance result. Region
and base URL must be explicit and 401 must not trigger silent cross-region
fallback.

On the ten-case public dataset, the global endpoint completed 30/30 requests
with P50 1.43 seconds and P95 1.87 seconds. Content markers passed in 80% of
attempts. Every response selected `lightmount_lite`; the JavaScript case returned
only a page shell and the PDF case returned an explanation rather than paper
text. This supports explicit Beta integration followed by engine-specific
JS/PDF evaluation, not immediate default routing.
