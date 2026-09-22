"""Initialize a standalone Map volume from locked archives with bounded disk use.

This file also runs standalone inside the upstream Map image (Python stdlib only).
aria2 supplies resumable parallel downloads; no database starts until all receipts exist.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tarfile
import time
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from urllib.error import HTTPError
from urllib.request import Request, urlopen

GIB = 1024**3
MAPPINGS = {
    "nominatim_volumes.tar": {
        "projects/metis2/docker/docker/volumes/nominatim-data/_data": "nominatim/postgres",
        "projects/metis2/docker/docker/volumes/nominatim-flatnode/_data": "nominatim/flatnode",
    },
    "osm_tile_server.tar": {
        "projects/ogma3/docker/volumes/osm-data/_data": "database",
    },
    "osrm_routing.tar": {profile: f"routing/{profile}" for profile in ("car", "bike", "foot")},
}


def require_space(root: Path, reserve: int, additional: int = 0) -> None:
    free = shutil.disk_usage(root).free
    if free < reserve + additional:
        raise RuntimeError(f"Disk guard: free={free}, reserve={reserve}, additional={additional}")


def verify_archive(path: Path, size: int, digest: str) -> None:
    if path.is_symlink() or path.stat().st_size != size:
        raise ValueError(f"Archive size/type mismatch: {path.name}")
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024**2), b""):
            checksum.update(block)
    if checksum.hexdigest() != digest:
        raise ValueError(f"Archive checksum mismatch: {path.name}")


def extract_archive(
    archive: Path | BinaryIO, root: Path, mappings: dict[str, str], reserve: int
) -> dict[str, int]:
    """One pass, allowlisted paths, sparse zero blocks, and no link traversal."""
    counts = {prefix: 0 for prefix in mappings}
    root = root.resolve()
    options = {"name": archive} if isinstance(archive, Path) else {"fileobj": archive}
    with tarfile.open(**options, mode="r|", bufsize=1024**2) as source:
        for member in source:
            name = member.name.removeprefix("./").rstrip("/")
            prefix = next((p for p in mappings if name == p or name.startswith(p + "/")), None)
            if prefix is None:
                continue
            relative = PurePosixPath(name).relative_to(prefix)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Unsafe archive path: {name}")
            target = root / mappings[prefix] / relative
            if not target.resolve().is_relative_to(root) or target.is_symlink():
                raise ValueError(f"Unsafe extraction target: {name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise ValueError(f"Unsupported archive member: {name}")
            # A frozen archive may contain the source server's PID lock. It is
            # not database content and must not identify a process on this host.
            if relative.name == "postmaster.pid":
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            reader = source.extractfile(member)
            assert reader is not None
            # Re-read and compare interrupted restores rather than rewriting
            # already correct database files. No bytes are trusted by size alone.
            existing = target.exists()
            with reader, target.open("r+b" if existing else "wb") as output:
                # Read each existing file sequentially before decoding its
                # replacement. Alternating archive/data reads per block causes
                # severe seek contention on rotational local-path storage.
                hashes = []
                if existing:
                    for old in iter(lambda: output.read(1024**2), b""):
                        hashes.append(hashlib.sha256(old).digest())
                    output.seek(0)
                index = 0
                remaining = member.size
                while remaining:
                    require_space(root, reserve, 1024**2)
                    block = reader.read(min(1024**2, remaining))
                    if not block:
                        raise ValueError(f"Truncated archive member: {name}")
                    if existing:
                        if index < len(hashes) and hashes[index] == hashlib.sha256(block).digest():
                            output.seek(len(block), os.SEEK_CUR)
                        else:
                            output.write(block)
                    elif block.count(0) == len(block):
                        output.seek(len(block), os.SEEK_CUR)
                    else:
                        output.write(block)
                    remaining -= len(block)
                    index += 1
                output.truncate(member.size)
            target.chmod(member.mode & 0o777)
            counts[prefix] += 1
    if not all(counts.values()):
        raise ValueError(f"Missing archive population: {counts}")
    return counts


def download(url: str, archive: Path, size: int, reserve: int) -> None:
    # st_blocks, unlike st_size, handles aria2's sparse, pre-sized partial file.
    allocated = archive.stat().st_blocks * 512 if archive.exists() else 0
    require_space(archive.parent, reserve, max(0, size - allocated))
    command = [
        "aria2c",
        "--continue=true",
        "--file-allocation=none",
        "--auto-file-renaming=false",
        "--allow-overwrite=false",
        "--max-connection-per-server=16",
        "--split=16",
        "--min-split-size=64M",
        "--max-overall-download-limit=64M",
        "--max-tries=8",
        "--retry-wait=10",
        "--connect-timeout=20",
        "--timeout=60",
        "--summary-interval=60",
        "--console-log-level=notice",
        "--download-result=hide",
        "--show-console-readout=false",
        "--dir",
        str(archive.parent),
        "--out",
        archive.name,
        url,
    ]
    with subprocess.Popen(command) as process:
        try:
            while process.poll() is None:
                require_space(archive.parent, reserve, 512 * 1024**2)
                time.sleep(2)
            if process.returncode:
                raise RuntimeError(f"Download failed: {archive.name}, exit={process.returncode}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def restore_compressed(
    packed: Path, root: Path, mappings: dict[str, str], size: int, digest: str, reserve: int
) -> dict[str, int]:
    """Hash every original tar byte while restoring; never materialize another tar.

    Restored files remain unqualified until the complete stream, including trailing
    tar padding, matches the frozen contract. No service starts before that point.
    """
    require_space(root, reserve, size)
    checksum = hashlib.sha256()
    received = 0
    with subprocess.Popen(["zstd", "-dc", str(packed)], stdout=subprocess.PIPE) as process:
        class CheckedStream:
            def read(self, count: int = -1) -> bytes:
                nonlocal received
                block = process.stdout.read(count)
                received += len(block)
                if received > size:
                    raise ValueError("Decompressed archive exceeds locked size")
                checksum.update(block)
                return block

        stream = CheckedStream()
        try:
            counts = extract_archive(stream, root, mappings, reserve)
            while stream.read(8 * 1024**2):
                require_space(root, reserve)
            if process.wait():
                raise RuntimeError(f"Decompression failed: {packed.name}")
            if received != size or checksum.hexdigest() != digest:
                raise ValueError(f"Archive checksum/size mismatch: {packed.name}")
            return counts
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def transport_size(url: str) -> int:
    # A controlled mirror can still be finalizing the next atomic .zst file.
    for attempt in range(61):
        try:
            with urlopen(Request(url, method="HEAD"), timeout=30) as response:
                return int(response.headers["Content-Length"])
        except HTTPError as error:
            if error.code != 404 or attempt == 60:
                raise
            if attempt == 0:
                print("Waiting for compressed source to finish", flush=True)
            time.sleep(30)
    raise AssertionError("unreachable")


def setup(
    root: Path, contract: dict, reserve: int, source_base: str = "", compression: str = "",
    query_only: bool = False,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    require_space(root, reserve)
    state = root / ".map-setup"
    state.mkdir(exist_ok=True)
    urls = {Path(url).name: url for url in contract["data_urls"]}
    if set(urls) != set(MAPPINGS):
        raise ValueError("Unexpected Map archive inventory")
    # Largest first bounds peak usage: never keep multiple source archives.
    for name in sorted(urls, key=lambda n: contract["data_archive_bytes"][n], reverse=True):
        mappings = {p: v for p, v in MAPPINGS[name].items()
                    if not (query_only and v == "nominatim/flatnode")}
        digest = contract["data_archive_sha256"][name]
        size = contract["data_archive_bytes"][name]
        archive = state / name
        receipt = state / (name + ".json")
        if receipt.exists():
            previous = json.loads(receipt.read_text())
            if previous.get("sha256") != digest or previous.get("bytes") != size:
                raise ValueError(f"Dataset revision requires a new volume: {name}")
            if previous.get("query_only", False) != query_only:
                raise ValueError(f"Dataset mode requires a new volume: {name}")
            if not all(any((root / p).rglob("*")) for p in mappings.values()):
                raise ValueError(f"Receipt exists but dataset is missing: {name}")
            archive.unlink(missing_ok=True)
            print(f"Already initialized: {name}", flush=True)
            continue
        if query_only and name == "nominatim_volumes.tar":
            # This osm2pgsql import/update cache is not used by the query API.
            # Keep the complete database; do not run freeze/drop on its tables.
            (root / "nominatim/flatnode/flatnode.file").unlink(missing_ok=True)
        print(f"Downloading: {name}", flush=True)
        needs_archive = (
            not archive.exists()
            or Path(str(archive) + ".aria2").exists()
            or archive.stat().st_size != size
        )
        if compression and needs_archive:
            if compression != "zstd" or not source_base:
                raise ValueError("Compressed transport requires an explicit zstd source")
            archive.unlink(missing_ok=True)
            Path(str(archive) + ".aria2").unlink(missing_ok=True)
            packed = state / (name + ".zst")
            url = source_base.rstrip("/") + "/" + packed.name
            packed_size = transport_size(url)
            if not 0 < packed_size <= size + 64 * 1024**2:
                raise ValueError("Unexpected compressed transport size")
            if (not packed.exists() or packed.stat().st_size != packed_size
                    or Path(str(packed) + ".aria2").exists()):
                download(url, packed, packed_size, reserve)
            print(f"Restoring and verifying SHA-256: {name}", flush=True)
            counts = restore_compressed(packed, root, mappings, size, digest, reserve)
        else:
            if needs_archive:
                download(
                    source_base.rstrip("/") + "/" + name if source_base else urls[name],
                    archive,
                    size,
                    reserve,
                )
            print(f"Verifying SHA-256: {name}", flush=True)
            verify_archive(archive, size, digest)
            require_space(root, reserve, size)
            print(f"Extracting: {name}", flush=True)
            counts = extract_archive(archive, root, mappings, reserve)
        os.sync()
        temporary = receipt.with_suffix(".tmp")
        temporary.write_text(json.dumps({"sha256": digest, "bytes": size, "files": counts,
                                         "query_only": query_only}))
        temporary.replace(receipt)
        os.sync()
        archive.unlink(missing_ok=True)
        (state / (name + ".zst")).unlink(missing_ok=True)
        Path(str(archive) + ".aria2").unlink(missing_ok=True)
        print(f"Initialized and removed archive: {name}", flush=True)
    print("All Map archives verified and initialized", flush=True)


def configure_browser_assets(assets: Path = Path("/app/public/assets")) -> None:
    """Keep precompressed JS identical to the image's patched same-origin JS."""
    for source in assets.glob("*.js"):
        content = source.read_bytes()
        if any(url in content for url in (b"http://localhost:8080/nominatim/",
                                           b"http://localhost:8080/osrm/")):
            raise ValueError(f"Browser service URL still uses localhost: {source.name}")
        packed = source.with_suffix(".js.gz")
        if packed.exists() and gzip.decompress(packed.read_bytes()) != content:
            packed.write_bytes(gzip.compress(content, mtime=0))


def configure_tiles(
    root: Path,
    cache: Path = Path("/var/cache/renderd/tiles"),
    apache: Path = Path("/etc/apache2/sites-enabled/tile-server.conf"),
    renderd: Path = Path("/etc/renderd.conf"),
) -> None:
    """Persist rendered tiles and allow bounded cold rendering on a shared host."""
    destination = root / "tiles"
    destination.mkdir(parents=True, exist_ok=True)
    if cache.is_symlink():
        if cache.resolve() != destination.resolve():
            raise ValueError("Unexpected tile cache link")
    else:
        owner = cache.stat()
        shutil.copytree(cache, destination, dirs_exist_ok=True)
        for path in [destination, *destination.rglob("*")]:
            os.chown(path, owner.st_uid, owner.st_gid)
        shutil.move(str(cache), str(cache.with_name(cache.name + ".image-baseline")))
        cache.symlink_to(destination, target_is_directory=True)
    config, count = re.subn(
        r"(?m)^([ \t]*)ModTileMissingRequestTimeout[ \t]+\d+[ \t]*$",
        r"\g<1>ModTileMissingRequestTimeout 120", apache.read_text(),
    )
    if count != 1:
        raise ValueError("Unexpected Apache tile timeout configuration")
    # Host load includes unrelated pods. CPU/memory limits and two render
    # workers bound this service instead of rejecting tiles at host load > 5.
    if "ModTileMaxLoadMissing" not in config:
        config = config.replace("</VirtualHost>", "    ModTileMaxLoadMissing 1000\n</VirtualHost>")
    apache.write_text(config)
    config, count = re.subn(r"(?m)^num_threads[ \t]*=[ \t]*\d+[ \t]*$",
                            "num_threads=2", renderd.read_text())
    if count != 1:
        raise ValueError("Unexpected render worker configuration")
    renderd.write_text(config)


def serve(root: Path, reserve: int, command: list[str]) -> int:
    """Stop only Map if the shared filesystem reaches the protected reserve."""
    require_space(root, reserve)
    with subprocess.Popen(command, start_new_session=True) as process:

        def stop(signum, frame):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)

        previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            while process.poll() is None:
                require_space(root, reserve)
                time.sleep(5)
            return process.returncode
        finally:
            stop(None, None)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            for sig, handler in previous.items():
                signal.signal(sig, handler)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/data"))
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--reserve-gib", type=int, default=64)
    parser.add_argument("--source-base", default="")
    parser.add_argument("--compression", choices=["", "zstd"], default="")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--query-only", action="store_true",
                        help="Omit osm2pgsql flatnode import/update cache for frozen queries")
    args = parser.parse_args()
    setup(
        args.root,
        json.loads(args.contract.read_text())["components"]["map"],
        args.reserve_gib * GIB,
        args.source_base,
        args.compression,
        args.query_only,
    )
    if args.serve:
        configure_browser_assets()
        configure_tiles(args.root)
        raise SystemExit(
            serve(
                args.root,
                args.reserve_gib * GIB,
                [
                    "/entrypoint.sh",
                    "/usr/bin/supervisord",
                    "-c",
                    "/etc/supervisor/conf.d/supervisord.conf",
                ],
            )
        )
