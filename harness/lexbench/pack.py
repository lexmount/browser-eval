"""Pack report evidence into lazy-loaded gzip files for the existing static host."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import shutil
import tempfile
from pathlib import Path

from harness.reporting.packs import write_packs


def package(source: Path, destination: Path, pack_limit: int = 8 * 1024 * 1024) -> dict:
    if source.is_symlink():
        raise ValueError("Report source must not be a symlink")
    source = source.resolve()
    if not source.is_dir():
        raise ValueError("Report source must be a directory")
    if destination.exists() or destination.is_symlink():
        raise ValueError("Output already exists; choose a new package directory")
    destination = destination.absolute()
    if destination.resolve().is_relative_to(source):
        raise ValueError("Output must not be inside the source report")
    if pack_limit <= 0:
        raise ValueError("pack_limit must be positive")
    paths = sorted(source.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("Report bundle must not contain symlinks")
    if any(
        part.startswith(".") or part == "node_modules"
        for p in paths
        for part in p.relative_to(source).parts
    ):
        raise ValueError("Report bundle contains an excluded path")
    files = [path for path in paths if path.is_file()]
    physical = {
        "index.html",
        "en.html",
        "en/index.html",
        "app.js",
        "style.css",
        "mermaid.min.js",
        "mermaid-LICENSE.txt",
    }
    physical.update(
        p.relative_to(source).as_posix() for p in files if p.is_relative_to(source / "assets")
    )
    if not all((source / name).is_file() for name in physical):
        raise ValueError("Report is missing a required entry or runtime asset")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".lexbench-package-", dir=destination.parent) as work:
        stage = Path(work) / "package"
        stage.mkdir()
        for name in sorted(physical):
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if name.endswith(".html"):
                text = (source / name).read_text()
                prefix = "../" if name.startswith("en/") else ""
                marker = f'<script defer src="{prefix}mermaid.min.js"></script>'
                if text.count(marker) != 1:
                    raise ValueError("HTML entry must have one Mermaid runtime marker")
                target.write_text(
                    text.replace(
                        marker, f'<script defer src="{prefix}bundle-loader.js"></script>' + marker
                    )
                )
            else:
                shutil.copy2(source / name, target)
        shutil.copy2(
            Path(__file__).with_name("assets") / "bundle-loader.js", stage / "bundle-loader.js"
        )
        metadata = {}

        def evidence():
            for path in files:
                name = path.relative_to(source).as_posix()
                if name in physical:
                    continue
                data = path.read_bytes()
                metadata[name] = {
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "mime": mimetypes.guess_type(name)[0] or "application/octet-stream",
                }
                yield name, data

        catalog, index = write_packs(
            evidence(),
            stage,
            limit=pack_limit,
            pattern="evidence-{index:02d}.bin.gz",
            compresslevel=6,
        )
        for name, entry in catalog.items():
            entry.update(metadata[name])
        (stage / "bundle-manifest.json").write_text(
            json.dumps({"files": catalog}, separators=(",", ":")) + "\n"
        )
        summary = {
            "bundled_files": len(catalog),
            "packs": index,
            "physical_files": sum(p.is_file() for p in stage.rglob("*")),
        }
        if destination.exists() or destination.is_symlink():
            raise ValueError("Output already exists; choose a new package directory")
        stage.rename(destination)
        return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(package(args.source, args.output)))


if __name__ == "__main__":
    main()
