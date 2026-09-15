"""Download apt's resolved package plan concurrently into its archive cache."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import hashlib
import shlex
import sys
import urllib.request


@dataclass(frozen=True)
class AptPackage:
    url: str
    destination: Path
    size: int
    algorithm: str
    digest: str


def parse_plan(plan: Path, archives: Path) -> list[AptPackage]:
    items = []
    for line in plan.read_text(encoding="utf-8").splitlines():
        if not line.startswith("'"):
            continue
        fields = shlex.split(line)
        if len(fields) != 4 or not fields[0].startswith(("http://", "https://")):
            raise ValueError(f"invalid apt package plan entry: {line}")
        name = fields[1]
        if name in {"", ".", ".."} or Path(name).name != name or "\\" in name:
            raise ValueError(f"unsafe apt package filename: {name}")
        kind, separator, digest = fields[3].partition(":")
        algorithms = {"MD5Sum": "md5", "SHA256": "sha256"}
        algorithm = algorithms.get(kind)
        if not separator or algorithm is None or len(digest) != hashlib.new(algorithm).digest_size * 2:
            raise ValueError(f"invalid apt package checksum: {fields[3]}")
        try:
            int(digest, 16)
            size = int(fields[2])
        except ValueError as exc:
            raise ValueError(f"invalid apt package size or checksum: {line}") from exc
        if size < 0:
            raise ValueError(f"invalid apt package size: {size}")
        items.append(AptPackage(fields[0], archives / name, size, algorithm, digest.lower()))
    return items


def download(item: AptPackage) -> None:
    temporary = item.destination.with_suffix(item.destination.suffix + ".part")
    for attempt in range(3):
        try:
            urllib.request.urlretrieve(item.url, temporary)
            if temporary.stat().st_size != item.size:
                raise ValueError(f"apt package size mismatch: {item.destination.name}")
            with temporary.open("rb") as handle:
                actual = hashlib.file_digest(handle, item.algorithm).hexdigest()
            if actual != item.digest:
                raise ValueError(f"apt package checksum mismatch: {item.destination.name}")
            temporary.replace(item.destination)
            return
        except Exception:
            temporary.unlink(missing_ok=True)
            if attempt == 2:
                raise


def main(plan: Path, archives: Path) -> None:
    archives.mkdir(parents=True, exist_ok=True)
    items = parse_plan(plan, archives)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(download, items))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
