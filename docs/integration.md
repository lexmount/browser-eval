# Integration decisions from measured behavior

## CowAgent

CowAgent currently owns URL validation, direct HTTP, HTML text stripping and
optional document parsing in `agent/tools/web_fetch/web_fetch.py`. It does not
call a managed Web Fetch supplier. The lowest-risk integration point is inside
that `WebFetch` class, behind an explicit provider setting—not a second
model-visible tool name.

Recommended first routing policy:

```text
public static HTML -> Lexmount Global beta route
PDF/document       -> CowAgent local parser until Lexmount PDF content passes
JavaScript page    -> browser tool or an explicitly selected rendering engine
login/interaction  -> browser tool, never WebFetch
```

The managed client should preserve CowAgent's existing model-facing result:

```python
result = lexmount.extract(url)
return ToolResult.success(
    f"Title: {result.get('title', '')}\n\nContent:\n{result.get('main_text', '')}"
)
```

Implementation details:

1. Keep CowAgent's `http/https` and SSRF validation before calling the supplier.
2. Read `LEXMOUNT_API_KEY` and `LEXMOUNT_PROJECT_ID` from CowAgent's environment
   configuration; never expose either in the tool schema or trace artifact.
3. Call only `https://api.lexmount.com/v1/extract` for this rollout. Do not
   retry `.cn` after 401.
4. Map 401 to configuration error, 429 to retryable rate limit, 5xx/timeout to
   upstream failure, and HTTP 200 with empty/missing `main_text` to content
   failure.
5. Record `provider`, `request_id`, `engine`, upstream status, elapsed time and
   content length. A fallback must record both attempts; otherwise reliability
   looks better while cost and latency disappear.
6. Gate rollout by category. The current evidence does not support making
   Lexmount the default for JS or PDF.

## Other agents and frameworks

Expose one stable operation with URL input and structured result:

```json
{
  "name": "web_fetch",
  "input": {"url": "https://example.com"},
  "output": {
    "title": "Example Domain",
    "content": "...",
    "final_url": "https://example.com/",
    "provider": "lexmount-global",
    "trace_id": "..."
  }
}
```

Adapters can implement an MCP tool, a framework provider interface, or a plain
HTTP client, but should not rename the capability per supplier. Search remains
a separate operation: `web_search(query)` discovers URLs, then `web_fetch(url)`
reads selected pages. Browserbase/Cloud Browser remains a separate escalation
path for clicks, auth, downloads and human takeover.

## Release gate

A supplier is eligible for default routing only after:

- transport and content pass meet category-specific thresholds;
- P95 is measured on successes and all attempts;
- fresh and cached behavior are separated;
- per-page usage units and failure charges are captured;
- full failed response bodies remain available in the private report;
- credential, SSRF and data-retention behavior are reviewed.
