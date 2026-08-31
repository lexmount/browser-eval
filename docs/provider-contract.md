# Web Fetch provider contract

A provider implements:

```python
fetch(case: EvalCase, timeout: float) -> ProviderResult
```

`ProviderResult.content` is always persisted, including API error bodies.
Providers should return normalized fields where available:

- request success and HTTP status;
- fetched content;
- final URL and title;
- provider-specific request ID, engine, server elapsed time and billing data;
- a stable error type and actionable error message.

Credentials are expanded from environment placeholders in the provider config.
They must never be added to `metadata`, content, manifest or logs.

To add another supplier, implement `Provider`, register the type in
`providers/base.py`, add an example configuration and write a fixture-backed
unit test. Metric and report code should not require changes.
