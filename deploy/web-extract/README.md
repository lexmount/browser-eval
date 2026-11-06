# Deploy and reproduce Web Extract

The [Compose file](compose.yaml) defines the complete six-service stack. The API
image builds from the root Dockerfile and locked Python environment. The Firecrawl
API and browser Dockerfiles add the same attestation launcher to their digest-pinned
upstream images; the other three images are pinned by digest. This runs each project's native extractor;
it does not train or generate a site-specific extraction program.

## Start the services

Run from the repository root with Docker Compose v2 and `jq`. Keep evaluation
credentials in the repository `.env`; [.env.example](../../.env.example) names the
required variables. Set `EXTRACTION_API_KEY`, `EXTRACTION_MODEL_API_KEY`,
`SNAPSHOT_ORIGINS`, `WEB_EXTRACT_POSTGRES_PASSWORD` and `WEB_EXTRACT_QUEUE_PASSWORD`.
Generate separate random hex passwords for the database and queue. The model
defaults are DeepSeek Flash at `https://api.deepseek.com` with a 1,000,000-token
context setting. Match these to the provider account you actually use.

Prepare both datasets and discover their frozen Cloudflare deployments using the
maintained [input-hosting commands](../../examples/web_extract/README.md#participants-and-inputs).
Set `SNAPSHOT_ORIGINS` to those exact HTTPS origins, separated by commas. This
allowlist deliberately excludes arbitrary live websites.

```sh
docker compose --env-file .env -f deploy/web-extract/compose.yaml config --quiet
docker compose --env-file .env -f deploy/web-extract/compose.yaml up --build -d --wait --wait-timeout 300
docker compose --env-file .env -f deploy/web-extract/compose.yaml ps
```

Only the authenticated extraction API is published, on `127.0.0.1:8080` by default.
The browser, native Firecrawl API, identity endpoint, database and queues remain on
the private Compose network. Three named volumes retain database and queue data.
The native Python image builds for the Docker host; the pinned Firecrawl images
use amd64, matching the Rainbond deployment. ARM hosts need Docker's amd64 emulation
for those four images. RabbitMQ uses the host architecture from its pinned
multi-platform digest, avoiding Erlang JIT failures under amd64 emulation. Resource limits reproduce the configured Rainbond limits,
not a measured minimum host requirement. The browser base uses the
[Millisecond Mirror GHCR proxy](https://1ms.run/guide/temp), retaining the exact
upstream manifest digest; image content is verified by that digest.

Each request runs in a fresh supervised process. Its browser and model client are
closed within that request, including detached browser cleanup after crashes and
timeouts. For latency runs, keep the API and browser on their preflight-qualified nodes or CPU classes.
Its runtime identity hashes the Linux host boot ID, so rescheduling cannot silently
reuse an old frozen configuration. Four CPU cores and one thread per BLAS library avoid the earlier CPU
quota and oversubscription bottlenecks. Stagehand exposes browser, navigation and
extraction times in `native.timings`; model reasoning settings remain unchanged.
Runtime identities bind installed source, native libraries and package data by
file hash. Startup rebuilds bytecode from the installed sources and binds those cache bytes
too. Each request verifies file signatures against a private hash cache and
rehashes changed files; fresh workers do not rewrite bytecode.
A running Firecrawl service rejects executable changes until restart. When
upgrading its browser, wait for browser health and identity before restarting
the Firecrawl API.

## Check every extraction route

Set `ENDPOINT` to `http://127.0.0.1:8080` for Compose, or your authenticated Rainbond
endpoint. Set `SNAPSHOT_URL` to one case URL from a verified snapshot binding.
Export `EXTRACTION_API_KEY` in the calling shell too; Compose's `--env-file`
does not export variables for subsequent `curl` commands.

```sh
curl --fail --silent --show-error "$ENDPOINT/health" \
  -H "Authorization: Bearer $EXTRACTION_API_KEY" > health.json
for engine in crawl4ai scrapegraph stagehand firecrawl; do
  jq --arg engine "$engine" --arg url "$SNAPSHOT_URL" '{
    url: $url,
    schema: {type: "object", properties: {name: {type: "string"}}, required: ["name"]},
    expected_runtime: .engines[$engine]
  }' health.json > request.json
  curl --fail --silent --show-error --max-time 120 \
    "$ENDPOINT/extract/$engine?keepalive=true" \
    -H "Authorization: Bearer $EXTRACTION_API_KEY" --json @request.json \
    | jq -e '.success == true and (.data | type == "object")'
done
```

These are deployment diagnostics, separate from benchmark results. Anonymous
`/health` returns liveness only; authenticated health supplies the full frozen
runtime identity. Keepalive addresses idle proxy timeouts while retaining the
110-second worker deadline.

Before streaming starts, authentication and malformed input use ordinary HTTP
errors. After the HTTP 200 headers have been sent, check the JSON `success` and
`http_status` fields too: a worker timeout is `success: false, http_status: 504`.
The native collector checks that envelope; HTTP status alone is insufficient.
This mode sends no additional model request. Set the reverse proxy read timeout
above the 120-second client budget where you control it. The frozen runtime must
advertise `json_keepalive: true` before the collector selects keepalive.

## Run both complete datasets

The [benchmark guide](../../examples/web_extract/README.md#freeze-and-run-the-full-population)
owns preparation, dataset selection, Cloudflare binding, preflight and certified
collection. Use the same commands for Zyte and SWDE, selecting each dataset's
explicit `--home` and its verified snapshot binding. The method IDs are
`crawl4ai-json`, `scrapegraph-oss-json`, `stagehand-local-json` and
`firecrawl-oss-json`.

Generate native options from the authenticated health response:

```sh
jq --arg endpoint "$ENDPOINT" '.engines as $engines | {
  "crawl4ai-json": "crawl4ai", "scrapegraph-oss-json": "scrapegraph",
  "stagehand-local-json": "stagehand", "firecrawl-oss-json": "firecrawl"
} | with_entries(.value = {
  endpoint: $endpoint, keepalive: true, runtime_identity: $engines[.value]
})' health.json > native-options.json
```

Pass `--native-options native-options.json` and all four methods to each `plan`
command. Prepare a separate assessment for every method; pass all four assessment
paths to the final plan. Start with `--concurrency 1 --execution-purpose quality
--serial-reason "Shared native worker and browser budget"` for this single API
instance. Freeze a higher capacity only after its bounded preflight succeeds.
Run the certified command once per method and score its complete output. A new
full run makes 560 Zyte and 3,200 SWDE logical calls, plus separately declared
preflights and allowed infrastructure repairs. It incurs model API usage.

For example, after preparing each dataset and saving its Cloudflare binding at
`$RUN_ROOT/{zyte,swde}/snapshots.json`, the complete collection sequence is:

```sh
for dataset in zyte swde; do
  home="$RUN_ROOT/$dataset"
  uv run --frozen python -m harness.web_extract.cli --home "$home" plan \
    --methods crawl4ai-json scrapegraph-oss-json stagehand-local-json firecrawl-oss-json \
    --native-options native-options.json --snapshots "$home/snapshots.json" \
    --concurrency 1 --execution-purpose quality \
    --serial-reason "Shared native worker and browser budget" \
    --output "$home/candidate.json"
  for method in crawl4ai-json scrapegraph-oss-json stagehand-local-json firecrawl-oss-json; do
    uv run --frozen python -m harness.web_extract.cli --home "$home" preflight \
      --profile "$home/candidate.json" --method "$method" \
      --run-dir "$home/preflight/$method" \
      --assessment-output "$home/$method-assessment.json"
  done
  uv run --frozen python -m harness.web_extract.cli --home "$home" plan \
    --methods crawl4ai-json scrapegraph-oss-json stagehand-local-json firecrawl-oss-json \
    --native-options native-options.json --snapshots "$home/snapshots.json" \
    --concurrency 1 --execution-purpose quality \
    --serial-reason "Shared native worker and browser budget" \
    --execution-assessment "$home/crawl4ai-json-assessment.json" \
    --execution-assessment "$home/scrapegraph-oss-json-assessment.json" \
    --execution-assessment "$home/stagehand-local-json-assessment.json" \
    --execution-assessment "$home/firecrawl-oss-json-assessment.json" \
    --output "$home/cohort.json"
  for method in crawl4ai-json scrapegraph-oss-json stagehand-local-json firecrawl-oss-json; do
    uv run --frozen python scripts/run_certified_benchmark.py --track web-extract \
      --home "$home" --profile "$home/cohort.json" --method "$method" \
      --run-dir "$home/runs/$method"
    uv run --frozen python -m harness.web_extract.cli --home "$home" score "$home/runs/$method"
  done
done
```

`RUN_ROOT` is an absolute directory outside the checkout. Install the locked
evaluation environment with `uv sync --frozen --extra web-extract-eval --extra test`
before running it. Dataset preparation requires the pinned SWDE source archive;
the [SWDE preparation section](../../examples/web_extract/README.md#swde-multi-domain-extraction)
identifies that archive and the exact command. A missing Cloudflare deployment
produces a deployment proposal for the named account rather than silently choosing
different input bytes.

Repeat an interrupted certified command with the same run directory and frozen
implementation to retain completed calls. Rebuild scores and reports from saved
responses without calling providers again. Report-instance profiles, reference
revisions and publication bindings remain alongside external evidence. The report
package retains its source profile and inspectable responses; restoring the
bound evidence is required to reproduce historical report values.

The flow is reproducible; new model calls need not reproduce historical text
byte for byte. The deployed model, OS/browser build and input/runtime hashes are
part of each new cohort. Historical scores reproduce from their frozen responses.

## Deploy the same services on Rainbond

Use the six service names, pinned images, environment values, resource limits,
dependencies and mounts from `compose.yaml` in the selected workspace:

1. Create the five private dependency components from the Compose definitions.
   Build `firecrawl-api` with `Dockerfile.firecrawl` and `firecrawl-browser` with
   `Dockerfile.firecrawl-browser`. After Git source detection, select `dockerfile`
   and the corresponding file under `deploy/web-extract/`. The root Dockerfile
   builds the Python API. The two dependency files share the same launcher and
   use literal pinned bases: this also works on Rainbond builders that ignore
   Docker build arguments. Check the actual base digest and running identity.
   Set `FIRECRAWL_SERVICE_KIND=browser` for the browser. Keep both identity ports
   (`3020`) private, alongside API `3002`, browser `3000`, Redis `6379`, RabbitMQ
   `5672` and PostgreSQL `5432`.
2. Retain the upstream working directories and the three persistent data paths
   from Compose. Inject database, queue and model credentials through Rainbond.
3. Create `native-extraction` from the repository's root Dockerfile. Apply its
   Compose environment, inject credentials through Rainbond, connect it to the
   Firecrawl backend, and expose only `8080` through the workspace ingress.
4. Wait for dependency identities and application health, then check all four
   extraction routes before creating a benchmark plan. The Firecrawl identity
   includes the actual browser bundle, dependencies, installed browser bytes
   and public rendering controls.

If a build is pending, read its scheduling warning before changing application
resources. A build-node pod limit is separate from extraction CPU throttling.
For this stateless API, a maintenance stop can free its own slot while the build
runs; restore the API after the image is ready. Keep its data services running.
The deployment binding owns the actual workspace, application and authenticated
API address; these are not defaults for another installation.

## Software fees and Firecrawl's license

Self-hosted library use has no per-request software fee under the listed open-source
licenses. This configuration calls the paid DeepSeek API. The report compares
that model usage separately; it does not allocate an existing server bill to each
request. Firecrawl Cloud subscriptions are a different product.

Firecrawl's server uses [AGPL-3.0](https://github.com/firecrawl/firecrawl/blob/main/LICENSE).
Commercial use is permitted. Preserve copyright and license notices; when
distributing covered binaries, provide corresponding source under the license.
If you modify the covered service and let users interact with it over a network,
offer those users its corresponding source, including the covered modifications
and necessary build/install scripts. SDKs and some UI components have their own
MIT licenses; these do not relicense the server. An independently running API
client is not automatically the same covered program. A combined or linked
derivative must be assessed as such; an HTTP wrapper alone is not a license
exemption. Obtain separate permission from the rights holder if you cannot meet
the applicable AGPL terms. See the [upstream license distinction](https://github.com/firecrawl/firecrawl#license)
and the [GNU network-use explanation](https://www.gnu.org/licenses/gpl-faq.html.en#UnreleasedModsAGPL).
