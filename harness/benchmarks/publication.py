"""Build inspectable Hugging Face releases from committed public definitions."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


@dataclass(frozen=True)
class DatasetRelease:
    """A public definition snapshot; working-tree files never enter a release."""

    track: str
    revision: str
    files: dict[str, bytes]
    source_repository: str | None = None
    card_body: str | None = None

    @classmethod
    def from_git(cls, checkout: Path, track: str, revision: str) -> DatasetRelease:
        if track not in {"webfetch", "websearch", "webmainbench", "simpleqa-verified"}:
            raise ValueError("Unknown benchmark track")
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("A release requires a full Git commit")
        kind = subprocess.check_output(["git", "cat-file", "-t", revision], cwd=checkout, text=True).strip()
        if kind != "commit":
            raise ValueError("A release requires a Git commit, not a tree or blob")
        prefix = f"src/lexbench_{track}/definitions/"
        hub = subprocess.run(["git", "cat-file", "-e", revision + ":definitions"],
                             cwd=checkout, capture_output=True).returncode == 0
        if hub:
            prefix = "definitions/"
        raw = subprocess.run(
            ["git", "archive", "--format=tar", revision, prefix, "LICENSE", "NOTICE", "THIRD_PARTY.md"],
            cwd=checkout, check=True, capture_output=True,
        ).stdout
        files = {}
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            for item in archive:
                if item.isdir() or item.name == "pax_global_header":
                    continue
                if not item.isfile():
                    raise ValueError("Public definitions cannot contain links or special files")
                name = "definitions/" + item.name[len(prefix):] if item.name.startswith(prefix) else item.name
                if name.startswith("definitions/") and Path(name).suffix not in {".json", ".jsonl", ".txt", ".md"}:
                    raise ValueError("Unexpected definition file: " + name)
                content = archive.extractfile(item).read()
                pointer = re.fullmatch(rb"version https://git-lfs.github.com/spec/v1\noid sha256:([0-9a-f]{64})\nsize ([0-9]+)\n", content)
                if pointer:
                    local = checkout / item.name
                    if local.is_symlink() or not local.resolve().is_relative_to(checkout.resolve()):
                        raise ValueError("Unsafe local LFS file")
                    content = local.read_bytes()
                    if digest(content) != pointer[1].decode() or len(content) != int(pointer[2]):
                        raise ValueError("Materialize the exact committed LFS object: " + item.name)
                files[name] = content
        body = None
        if hub:
            body = subprocess.check_output(["git", "show", revision + ":README.md"], cwd=checkout, text=True)
            if body.startswith("---\n"):
                body = body.split("---", 2)[2].strip()
        repository = f"https://huggingface.co/datasets/Lexmount/lexbench-{track}" if hub else None
        return cls(track, revision, files, repository, body)

    def datasets(self) -> dict[str, dict]:
        registry = json.loads(self.files["definitions/registry.json"])
        if self.track == "webfetch":
            return {row["id"]: {
                "path": row["directory"] + "/tasks.json",
                "sha256": row["files"]["tasks.json"],
                "count": row["case_count"],
            } for row in registry["suites"]}
        return registry["datasets"]

    def build(self, output: Path) -> dict:
        if output.exists() and any(output.iterdir()):
            raise ValueError("Release output must be empty")
        files = dict(self.files)
        configurations = []
        for identity, binding, cases in self.records():
            identity_field = binding.get("id_field", "id")
            rows = [{
                "id": case[identity_field], "input": case.get("query", case.get("url", "")),
                "family": case.get("family", case.get("task_kind", "")),
                "language": case.get("language", ""),
                "task": json.dumps(case, ensure_ascii=False, sort_keys=True),
            } for case in cases]
            files[f"data/{identity}.jsonl"] = b"".join(
                (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode() for row in rows)
            configurations.append(
                f"- config_name: {identity}\n  data_files:\n  - split: test\n    path: data/{identity}.jsonl")
        manifest = {
            "schema": "browser-eval-dataset-release/1", "track": self.track,
            "source_repository": self.source_repository or f"https://github.com/lexmount/lexbench-{self.track}",
            "source_revision": self.revision, "datasets": self.datasets(),
            "files": {name: {"sha256": digest(raw), "size": len(raw)} for name, raw in sorted(files.items())},
        }
        files["manifest.json"] = encoded(manifest)
        output.mkdir(parents=True, exist_ok=True)
        for name, raw in files.items():
            path = output / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        return self._finish(output, manifest, configurations)

    def records(self):
        datasets = self.datasets()
        if not isinstance(datasets, dict) or not datasets:
            raise ValueError("A release requires registered datasets")
        for identity, binding in datasets.items():
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", identity):
                raise ValueError("Unsafe dataset identity")
            name = "definitions/" + binding["path"]
            if name not in self.files:
                raise ValueError("Unknown dataset definition: " + identity)
            raw = self.files[name]
            if digest(raw) != binding["sha256"]:
                raise ValueError("Dataset hash mismatch: " + identity)
            if binding.get("format", "json") not in {"json", "jsonl"}:
                raise ValueError("Unknown dataset format: " + identity)
            cases = ([json.loads(line) for line in raw.splitlines() if line.strip()]
                     if binding.get("format") == "jsonl" else json.loads(raw)["cases"])
            identity_field = binding.get("id_field", "id")
            if (not isinstance(cases, list) or not cases or any(
                    not isinstance(case, dict) or not isinstance(case.get(identity_field), str)
                    or not case[identity_field].strip() for case in cases)):
                raise ValueError("Dataset records require nonempty string identities: " + identity)
            ids = [case[identity_field] for case in cases]
            if type(binding["count"]) is not int or len(ids) != binding["count"] or len(set(ids)) != len(ids):
                raise ValueError("Dataset population mismatch: " + identity)
            yield identity, binding, cases

    def _finish(self, output: Path, manifest: dict, configurations: list[str]) -> dict:
        buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0, filename="") as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for name, raw in sorted(self.files.items()):
                    item = tarfile.TarInfo(name)
                    item.size, item.mode, item.mtime = len(raw), 0o644, 0
                    archive.addfile(item, io.BytesIO(raw))
        bundle = buffer.getvalue()
        (output / "definitions.tar.gz").write_bytes(bundle)
        manifest["archive"] = {"path": "definitions.tar.gz", "sha256": digest(bundle), "size": len(bundle)}
        (output / "manifest.json").write_bytes(encoded(manifest))
        title = {"webfetch": "LexBench Web Fetch", "websearch": "LexBench Web Search",
                 "simpleqa-verified": "LexBench SimpleQA Verified", "webmainbench": "LexBench WebMainBench"}[self.track]
        license_field = "license:\n- apache-2.0\n- mit" if self.track in {"websearch", "simpleqa-verified"} else "license: apache-2.0"
        card = "---\n" + license_field + "\nlanguage:\n- en\n- zh\ntags:\n- benchmark\n- evaluation\nconfigs:\n"
        header = card + "\n".join(configurations) + "\n---\n\n"
        card += "\n".join(configurations) + "\n---\n\n# " + title + "\n\n"
        card += (
            "Versioned task inputs, acceptance requirements and reference bindings for Browser Eval. "
            "The dataset viewer exposes each task; the `task` column preserves its complete definition as JSON.\n\n"
            "`definitions/` contains the original files. `definitions.tar.gz` contains identical bytes for a single-download installation. "
            "`manifest.json` records their hashes and the source Git commit. Select a full Hugging Face commit when downloading; tags are release labels. "
            "Scoring and execution code are maintained in [Browser Eval](https://github.com/lexmount/browser-eval).\n\n"
            "Project-authored material uses Apache-2.0. Upstream data retains its original terms; read `THIRD_PARTY.md`, `NOTICE` and any notices under `definitions/`. "
            "Full website captures, documents, screenshots and measured provider outputs are excluded. Some source references require separately acquired inputs.\n"
        )
        if self.card_body:
            card = header + self.card_body.strip() + "\n"
        (output / "README.md").write_text(card)
        return {**manifest, "archive": {"path": "definitions.tar.gz", "sha256": digest(bundle), "size": len(bundle)}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--track", choices=["webfetch", "websearch", "simpleqa-verified", "webmainbench"], required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--publish", metavar="HF_REPOSITORY", help="Upload the built release to this dataset")
    args = parser.parse_args()
    result = DatasetRelease.from_git(args.checkout, args.track, args.revision).build(args.output)
    print(json.dumps({"track": result["track"], "source_revision": result["source_revision"], "archive": result["archive"]}))
    if args.publish:
        from huggingface_hub import HfApi
        api = HfApi()
        info = api.repo_info(repo_id=args.publish, repo_type="dataset")
        current = {p.relative_to(args.output).as_posix() for p in args.output.rglob("*") if p.is_file()}
        removed = [p.rfilename for p in info.siblings if p.rfilename not in current and p.rfilename != ".gitattributes"]
        commit = api.upload_folder(repo_id=args.publish, repo_type="dataset", folder_path=args.output,
                                   parent_commit=info.sha, delete_patterns=removed,
                                   commit_message="Publish verified " + args.track + " definitions")
        print(json.dumps({"repository": args.publish, "revision": commit.oid}))


if __name__ == "__main__":
    main()
