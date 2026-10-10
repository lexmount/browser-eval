# WebArena environments

`harness/environments/webarena/` maintains Docker and Rainbond environments. Tasks and evaluators come from the pinned `webarena-verified` dependency. Image identity has one authority: `evaluation/browser-use-public/webarena-environment-images.json`.

`runtime.example.json` supplies local Docker hosts and ports. `site-delivery-contract.json` defines internal ports, initialization and read-only Map data mounts. Map is optional; a contract does not trigger download or deployment. Stateful sites recreate their initial databases from images without sharing writable database directories between slices. Rainbond GitLab needs its own 2 GiB `/dev/shm` memory mount.

Copy runtime configuration outside Git, set `hostname` and use an absolute image-lock path:

```sh
uv run --frozen python scripts/manage_webarena_environment.py verify --config <config> --site reddit
uv run --frozen python scripts/manage_webarena_environment.py start --config <config> --site reddit --receipt <receipt>
uv run --frozen python scripts/manage_webarena_environment.py reset --config <config> --site reddit --receipt <receipt>
uv run --frozen python scripts/verify_webarena_env_contract.py
```

See the [run example](../../examples/trajectory_replay/public_webarena/README.md) for Rainbond slices, leases, concurrent access checks and cleanup. Runtime configurations, addresses, credentials and receipts stay outside Git; reusable code and configuration templates can be committed.

Docker management and Map verification contracts derive from [`lexmount/webarena-env`](https://github.com/lexmount/webarena-env/tree/daecdf8b46e71ccc70d7cc2fdaf92a83af899624), retaining its [MIT license](LICENSE).

## Shared Map on Rainbond

Deploy Map separately from the four writable sites, as a single instance. Stop it before updates so two PostgreSQL processes never open the same data directory during a rollout. Queries share frozen data, but PostgreSQL, application databases and tile caches need writable storage; do not mount the whole volume read-only.

The entry point uses `--query-only`, retaining the complete query database, tiles and all three routing datasets while omitting Nominatim's `flatnode.file` import/update cache. Evaluation does not update map data. The omitted file remains part of the original archive's SHA-256 verification, and the archives support full restoration. This mode cannot perform incremental imports; a mode change requires a new volume. See [Nominatim's frozen-data guidance](https://nominatim.org/release-docs/4.5/admin/Import/#dropping-data-required-for-dynamic-updates).

Use the Map digest from the image lock. Persist `/map-data` and `/var/lib/postgresql/14/main` separately. Mounting over the image's entire `/data` would hide its initial styles. Mount these files and set CMD to `/bin/bash /opt/map-bootstrap.sh`:

| Repository file | Container path |
| --- | --- |
| `deploy/webarena-rainbond/map-bootstrap.sh` | `/opt/map-bootstrap.sh` |
| `harness/environments/webarena/map_setup.py` | `/opt/map_setup.py` |
| `harness/environments/webarena/map_detail.py` | `/opt/map_detail.py` |
| `deploy/webarena/site-delivery-contract.json` | `/opt/map-contract.json` |

Startup links `/var/cache/renderd/tiles` to `/map-data/tiles` to preserve rendered tiles across recreation. Rendering concurrency is 2; a missing tile gets up to 120 seconds. Use container resource limits so other shared-node workloads do not cause tile rejection. Readiness checks `/tile/0/0/0.png` before admitting traffic. Startup also synchronizes gzip copies of compiled JavaScript; stale compressed assets would otherwise send remote browsers to `localhost`. Requests remain same-origin.

The image's Rails OSM object database contains no nodes, ways or relations. Search uses restored Nominatim data, so the original object links would return 404. The detail endpoint queries that same frozen data by OSM type and ID, displaying names, addresses, coordinates and available tags at the original `/node/`, `/way/` and `/relation/` paths. Required route relations absent from Nominatim accept only exact records bound to a fixed Wikidata revision, OSM ID and source hash. Other missing objects still return 404; the endpoint does not invent content or claim full OSM history or membership.

The three original archives total about 174.5 GiB. Initialization downloads, verifies, sparsely extracts and removes each archive, starting with Nominatim. Budget for the current archive, extracted data and a node free-space reserve of at least 64 GiB. Download, extraction and serving check free space; below the reserve, Map stops and retains data. A declared volume capacity does not replace node disk checks or real storage quotas. Download uses at most 16 connections and 64 MiB/s, resuming partial files with aria2 state. Completion receipts require checksum verification and complete extraction; restarts need no download server. Recovery reads existing files sequentially, compares block hashes and rewrites only differing bytes. File size alone never proves correctness, and alternating archive/database reads would cause excessive seeks on rotating disks.

Downloads default to the contract's official S3 URLs. To reuse original archives, set `MAP_ARCHIVE_BASE_URL` to a controlled source supporting HEAD and HTTP Range. If it serves same-named `.tar.zst` files, set `MAP_ARCHIVE_COMPRESSION=zstd`. Streaming restoration verifies every original tar byte, including trailing padding, without storing another full tar. Serving starts only after all original lengths and hashes pass. Partial failed restores remain incomplete and are overwritten on recovery. Remove compressed transfer copies and the transfer service after delivery verification; retain original data.

Acceptance checks cover the home page, search, reverse geocoding, all three travel modes and real tile images, then repeat after restart. Expose only website port 8080; keep control port 8877 private.

```sh
uv run --frozen python scripts/verify_webarena_map.py \
  --url <map-origin> --output <external-verification-receipt.json>
```

After configuration changes, stop and redeploy Map, then verify actual container file hashes. A restart may retain an earlier configuration snapshot. During data initialization, avoid liveness probes that require site readiness and repeatedly restart downloads. Enable HTTP readiness checks after preparation completes.
