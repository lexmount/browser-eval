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
from dataclasses import dataclass, replace
from pathlib import Path

CARDS = Path(__file__).with_name("cards")


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
    language_annotations: dict | None = None
    tooling_revision: str | None = None

    @classmethod
    def from_git(cls, checkout: Path, track: str, revision: str) -> DatasetRelease:
        if track not in json.loads((CARDS / "metadata.json").read_bytes()):
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
            committed = subprocess.run(
                ["git", "show", revision + ":manifest.json"],
                cwd=checkout, capture_output=True,
            )
            if committed.returncode:
                raise ValueError("Hub source requires a committed dataset release manifest")
            identity = json.loads(committed.stdout)
            if (not isinstance(identity, dict)
                    or identity.get("schema") != "browser-eval-dataset-release/1"
                    or identity.get("track") != track):
                raise ValueError("Hub source manifest does not match the requested benchmark track")
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
                allowed = (Path(name).suffix in {".json", ".jsonl", ".txt", ".md"}
                           or Path(name).name in {"LICENSE", "NOTICE"}
                           or (track == "wcxb" and name.endswith(".html.gz")))
                if name.startswith("definitions/") and not allowed:
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
        repository = f"https://huggingface.co/datasets/Lexmount/lexbench-{track}" if hub else None
        return cls(track, revision, files, repository)

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
        if self.tooling_revision is not None and not re.fullmatch(r"[0-9a-f]{40}", self.tooling_revision):
            raise ValueError("Tooling requires a full Git commit")
        import yaml

        body = (self.card_body if self.card_body is not None
                else (CARDS / (self.track + ".md")).read_text(encoding="utf-8"))
        files = dict(self.files)
        configurations = []
        summary = []
        languages = set()
        used_annotations = {}
        metadata = json.loads((CARDS / "metadata.json").read_bytes())[self.track]
        annotations = self.language_annotations
        if annotations is None:
            annotations = json.loads((CARDS / "languages.json").read_bytes()).get(self.track, {})
        for identity, binding, cases in self.records():
            identity_field = binding.get("id_field", "id")
            groups = {}
            partition = metadata.get("partitions", {}).get(identity)
            for case in cases:
                case_id = case[identity_field]
                annotation = annotations.get(identity, {}).get(case_id)
                if annotation is not None:
                    if digest(encoded(case)) != annotation["case_sha256"]:
                        raise ValueError("Language annotation does not match frozen task: " + case_id)
                    language = annotation["language"]
                    used_annotations.setdefault(identity, {})[case_id] = annotation
                    languages.update(annotation.get("languages", [language]))
                else:
                    language = "und"
                    for field in metadata["language_fields"]:
                        value = case
                        for part in field.split("."):
                            value = value.get(part) if isinstance(value, dict) else None
                        if isinstance(value, str) and value.strip():
                            language = value.strip().lower().split("-")[0]
                            language = "mul" if language == "multilingual" else language
                            break
                    languages.add(language)
                row = {
                    "id": case_id,
                    "input": next((case[field] for field in metadata.get("input_fields", ["query", "url"])
                                   if isinstance(case.get(field), str)), ""),
                    "source_suite": identity,
                    "family": case.get("family") or case.get("task_kind") or case.get("track") or metadata.get("family", ""),
                    "language": language,
                    "task": json.dumps(case, ensure_ascii=False, sort_keys=True),
                }
                row.update({key: case[key] for key in ("track", "category")
                            if case.get(key) not in (None, "")})
                category_field = metadata.get("category_field")
                if "category" not in row and category_field and case.get(category_field):
                    row["category"] = case[category_field]
                name = identity
                if partition:
                    value = case.get(partition["field"])
                    if value not in partition["values"]:
                        raise ValueError("Unregistered viewer task group: " + str(value))
                    name = partition["values"][value]
                groups.setdefault(name, []).append(row)
            for name, rows in groups.items():
                path = f"data/{name}.jsonl"
                if any(item["config_name"] == name for item in configurations):
                    raise ValueError("Duplicate viewer configuration: " + name)
                files[path] = b"".join(
                    (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode() for row in rows)
                configurations.append({"config_name": name, "data_files": [{"split": "test", "path": path}]})
                summary.append({"config": name, "source_suite": identity, "count": len(rows),
                                "description": metadata["subsets"].get(name, "Registered task inputs")})
        manifest = {
            "schema": "browser-eval-dataset-release/1", "track": self.track,
            "source_repository": self.source_repository or f"https://github.com/lexmount/lexbench-{self.track}",
            "source_revision": self.revision, "datasets": self.datasets(),
            "viewer": {"configurations": summary, "language_annotations": used_annotations},
            "files": {name: {"sha256": digest(raw), "size": len(raw)} for name, raw in sorted(files.items())},
        }
        if self.tooling_revision is not None:
            manifest["tooling"] = {"repository": "https://github.com/lexmount/browser-eval",
                                   "revision": self.tooling_revision}
        files["manifest.json"] = encoded(manifest)
        output.mkdir(parents=True, exist_ok=True)
        for name, raw in files.items():
            path = output / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        return self._finish(output, manifest, configurations, metadata, languages, body, yaml)

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

    def _finish(self, output: Path, manifest: dict, configurations: list[dict], metadata: dict,
                languages: set[str], body: str, yaml) -> dict:

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
        header = {key: metadata[key] for key in ("pretty_name", "license", "tags", "task_categories", "source_datasets")}
        header["language"] = sorted(languages - {"und", "zxx", "mul"}) or ["und"]
        header["configs"] = configurations
        populations = manifest["viewer"]["configurations"]
        table = ["| Viewer group | Source suite | Tasks | Scope |", "| --- | --- | ---: | --- |"]
        table.extend(f"| `{row['config']}` | `{row['source_suite']}` | {row['count']:,} | {row['description']} |" for row in populations)
        body = body.replace("{{dataset_summary}}", "\n".join(table))
        body = body.replace("{{total_records}}", f"{sum(row['count'] for row in populations):,}")
        checkout = ""
        if self.tooling_revision is not None:
            body = body.replace("https://github.com/lexmount/browser-eval/blob/main/",
                                f"https://github.com/lexmount/browser-eval/blob/{self.tooling_revision}/")
            checkout = "git checkout " + self.tooling_revision + "\n"
        body = body.replace("{{tooling_checkout}}", checkout)
        card = "---\n" + yaml.safe_dump(header, sort_keys=False, allow_unicode=True) + "---\n\n" + body.strip() + "\n"
        (output / "README.md").write_text(card, encoding="utf-8")
        return {**manifest, "archive": {"path": "definitions.tar.gz", "sha256": digest(bundle), "size": len(bundle)}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--track", choices=sorted(json.loads((CARDS / "metadata.json").read_bytes())), required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tooling-revision", help="Full Browser Eval commit for executable examples and code links")
    parser.add_argument("--publish", metavar="HF_REPOSITORY", help="Upload the built release to this dataset")
    args = parser.parse_args()
    release = DatasetRelease.from_git(args.checkout, args.track, args.revision)
    if args.tooling_revision is not None:
        release = replace(release, tooling_revision=args.tooling_revision)
    result = release.build(args.output)
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
