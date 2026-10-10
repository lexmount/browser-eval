# WebArena fixed-trajectory replay: environment and reproduction

Formal replay runs the same public WebArena human trajectories in both browsers, without an Agent, model or LLM judge. The pinned evaluator checks final responses, service state and HAR.

## Code and dependencies

- `harness/environments/webarena/` owns Docker and Rainbond site lifecycle, initialization and machine-readable receipts. `deploy/webarena/` contains deployment configurations and data-mount contracts.
- `harness/trajectory_replay/public_webarena/` owns recording inventories, offline repairs, qualification gates, Chromium/Moli images, action replay, HAR, evaluator calls, one failure retry, first/final success statistics, evidence audit and separate report publication.

WebArena-Verified 1.2.3 supplies tasks and evaluators as a dependency. Official images are pinned in `evaluation/browser-use-public/webarena-environment-images.json`.

For a new experiment, run `uv run --frozen python scripts/lock_webarena_images.py --output /absolute/path/new-images.json`. It reuses environment site definitions, verifies registry digests and manifest bytes, and writes only a new file. Historical experiments use their bound image locks; resolving `latest` again does not reproduce an old image.

Git alone cannot restore a previous run byte for byte. Screenshots, HAR, attempts, manifests and summaries remain outside Git. `evaluation/browser-use-public/current-report.json` retains current public results and external evidence hashes to verify report bindings, not to replace a new run.

The public recording inventory contains 179 candidates: 30 `eligible` and 149 `skipped`. Formal runners execute only eligible tasks; candidate count is not the run denominator.

## Host and architecture

Use a Linux x86-64 Docker host, Intel or AMD. The current formal 30-task suite uses `linux/amd64` sites. Architecture rules exclude the `linux/arm64` Wikipedia tasks before execution; cross-architecture emulation is disabled. The browser measurement daemon must provide cgroup v2 CPU quota. Sites may use the same daemon or a separate Unix socket explicitly named in external runtime configuration.

## Reproduce on a new host

1. Check out and pin this repository's commit. Run `make setup`, `make ci` and `uv run --frozen python scripts/verify_webarena_env_contract.py`. Deployment and evaluation configurations must use the same image lock, ports and credential variable names.
2. Copy `deploy/webarena/runtime.example.json` outside Git, set a reachable `hostname` and an absolute `image_lock` path. Verify with `uv run --frozen python scripts/manage_webarena_environment.py verify --config <config> --site reddit`. Use `start` or `reset` to start or recreate sites, saving a receipt with `--receipt <path>`. Prepare Map data only when the selected tasks need it: `setup --config <config> --site map --data-dir <directory>` verifies three archive hashes before official initialization. The current Rainbond deployment does not include Map. Supply accounts only through environment variables.
3. Copy `examples/trajectory_replay/public_webarena/webarena.config.example.json` locally with the same ports, image digests, site URLs and credential variable names. Run environment smoke checks for CDP, login, HAR and origins. The example shares the local Docker daemon and assumes no personal rootless socket. If using separate daemons, set both sockets explicitly.
4. Run the frozen paired suite:

```text
uv run --frozen python -m harness.trajectory_replay.public_webarena.cli \
  --suite evaluation/browser-use-public/webarena-human-suite.json \
  --webarena-config <runtime-config> --env-file .env \
  --browser-docker-host unix:///var/run/docker.sock \
  --engines chromium,moli --failure-retries 1 \
  --output <evidence>/formal/webarena-public-trajectories/run \
  --reports <evidence>/formal/webarena-public-trajectories/reports
```

5. Run `examples/trajectory_replay/public_webarena/verify_metrics.py` and the report packager. Missing responses, actions, termination reasons or scores are failures. After entering the browser phase, missing screenshots, final pages or HAR are also failures.

Verify the WebArena-Verified version and required site-image digests before formal calls. Mutable tags, cross-architecture emulation and manual database changes cannot substitute for the pinned environment.

Executors support Docker recreation and receipt-bound Rainbond slice recreation. Each concurrent worker needs an exclusive complete writable slice; read-only Wikipedia may be shared. See the [Rainbond example](../examples/trajectory_replay/public_webarena/README.md). Concurrent HTTP checks establish reachability only; formal executions need receipts bound to their reset, images, initial state and readiness checks. A shared amd64 Kiwix deployment differs from the official arm64 image and cannot reuse that image's verification receipt.
