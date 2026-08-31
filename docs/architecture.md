# Architecture

Browser Harness Eval treats an agent-facing browser harness as a collection of
tools. `web_fetch` is the first implemented tool; browser interaction can be
added later without changing the run artifact contract.

## Four independent contracts

1. **Dataset** — defines what is evaluated: URLs, capability categories and
   deterministic acceptance markers. It contains no provider configuration.
2. **Provider** — defines how one method calls a service or local
   implementation. It contains no scoring logic.
3. **Metrics** — converts persisted output into deterministic per-attempt
   measurements and aggregates them. It contains no provider-specific rules.
4. **Report** — renders only persisted manifests, JSONL and text artifacts. It
   does not rerun requests or silently repair results.

## Run artifact contract

```text
run-directory/
├── manifest.json        # environment, parameters and selected methods
├── dataset.json         # immutable dataset snapshot
├── results.jsonl        # one structured record per attempt
├── summary.json         # reproducible aggregates
├── artifacts/           # full provider output, including error bodies/traces
└── report/
    ├── index.html
    ├── datasets/        # dataset index and one page per case
    ├── providers/       # method index and one page per provider
    ├── metrics/         # formal metric definitions
    ├── attempts/        # one diagnostic page per request
    └── artifacts/       # copied full content for portable offline viewing
```

Every attempt is retained. A network exception is serialized as a classified
failure and its traceback becomes the artifact. An HTTP/API failure retains its
response body. A transport success that misses content markers is reported as a
separate content-validation failure.
