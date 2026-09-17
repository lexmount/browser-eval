"""Check the exact Map archives before initializing persistent data volumes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_CONTRACT = (
    Path(__file__).resolve().parents[3] / "deploy/webarena/site-delivery-contract.json"
)


def verify_archives(
    data_dir: Path, contract_path: Path = DEFAULT_CONTRACT
) -> list[dict[str, str | int]]:
    """Check each Map archive against the contract size and SHA-256, failing on drift."""
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    map_site = contract["components"]["map"]
    expected = map_site["data_archive_sha256"]
    expected_bytes = map_site["data_archive_bytes"]
    names = [Path(urlparse(url).path).name for url in map_site["data_urls"]]
    if (
        len(names) != len(set(names))
        or set(names) != set(expected)
        or set(names) != set(expected_bytes)
    ):
        raise ValueError("Map archive checksum inventory differs from upstream URLs")
    rows: list[dict[str, str | int]] = []
    for name in names:
        archive = data_dir / name
        observed_bytes = archive.stat().st_size
        if observed_bytes != expected_bytes[name]:
            raise ValueError(f"Map archive size mismatch: {name}")
        digest = hashlib.sha256()
        with archive.open("rb") as source:
            for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
        observed = digest.hexdigest()
        if observed != expected[name]:
            raise ValueError(f"Map archive checksum mismatch: {name}")
        rows.append({"archive": name, "sha256": observed, "bytes": observed_bytes})
    return rows


def main() -> None:
    """Run the archive verification CLI before initializing persistent Map volumes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    args = parser.parse_args()
    print(json.dumps({"status": "pass", "archives": verify_archives(args.data_dir, args.contract)}))


if __name__ == "__main__":
    main()
