#!/bin/bash
# CMD for the digest-pinned upstream Map image; keep its native ENTRYPOINT.
set -euo pipefail
root=/map-data
needs_data=false
for archive in nominatim_volumes.tar osm_tile_server.tar osrm_routing.tar; do
    if [ ! -f "$root/.map-setup/$archive.json" ]; then needs_data=true; fi
done
args=(--root "$root" --contract /opt/map-contract.json --reserve-gib 64 --serve --query-only)
if [ "$needs_data" = true ]; then
    if ! command -v aria2c >/dev/null || ! command -v zstd >/dev/null; then
        apt-get -o Dir::Etc::sourceparts=- -o Acquire::Retries=2 \
            -o Acquire::http::Timeout=20 update > "$root/apt-update.log" 2>&1
        DEBIAN_FRONTEND=noninteractive apt-get -o Dir::Etc::sourceparts=- \
            install -y --no-install-recommends aria2 zstd > "$root/apt-install.log" 2>&1
        rm -rf /var/lib/apt/lists/*
    fi
    source_base=$(printenv MAP_ARCHIVE_BASE_URL || true)
    compression=$(printenv MAP_ARCHIVE_COMPRESSION || true)
    if [ -n "$source_base" ]; then
        args+=(--source-base "$source_base")
        if [ "$compression" = zstd ]; then
            args+=(--compression zstd)
        fi
    fi
fi
for folder in database nominatim routing tiles; do
    mkdir -p "$root/$folder"
    if [ ! -L "/data/$folder" ]; then
        if [ -e "/data/$folder" ]; then
            mv "/data/$folder" "/data/$folder.image-baseline"
        fi
        ln -s "$root/$folder" "/data/$folder"
    fi
done
exec nice -n 10 python3 /opt/map_setup.py "${args[@]}"
