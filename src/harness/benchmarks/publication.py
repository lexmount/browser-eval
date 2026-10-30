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
from fnmatch import fnmatchcase
from dataclasses import dataclass, replace
from pathlib import Path

CARDS = Path(__file__).with_name("cards")
HEADLESS_REPOSITORY = "https://github.com/lexmount/Lexbench-Headless-Browser"
HEADLESS_INPUTS = ("tasks", "fixtures", "config", "manifest.json", "harness_pins.json",
                   "LICENSE", "THIRD_PARTY_NOTICES.md")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _git_blobs(checkout: Path, revision: str, paths: tuple[str, ...]) -> dict[str, bytes]:
    """Read original data blobs; Git export attributes cannot rewrite their bytes."""
    tree = subprocess.check_output(
        ["git", "ls-tree", "-rz", revision, "--", *paths], cwd=checkout)
    entries = []
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        attributes, raw_name = entry.split(b"\t", 1)
        mode, kind, object_id = attributes.split()
        name = raw_name.decode("utf-8")
        if kind != b"blob" or mode not in {b"100644", b"100755"}:
            raise ValueError("Public definitions cannot contain links or special files: " + name)
        entries.append((name, object_id))
    raw = subprocess.run(["git", "cat-file", "--batch"], cwd=checkout, check=True,
                         input=b"\n".join(oid for _, oid in entries) + b"\n",
                         capture_output=True).stdout
    files, cursor = {}, 0
    for name, object_id in entries:
        end = raw.index(b"\n", cursor)
        header = raw[cursor:end].split()
        if len(header) != 3 or header[:2] != [object_id, b"blob"]:
            raise ValueError("Unavailable committed input: " + name)
        size = int(header[2])
        content = raw[end + 1:end + 1 + size]
        cursor = end + size + 2
        files[name] = content
    return files


def _headless_definitions(checkout: Path, revision: str) -> dict[str, bytes]:
    originals = _git_blobs(checkout, revision, HEADLESS_INPUTS)
    files, source_files = {}, {}
    for name, content in originals.items():
        if set(Path(name).parts) & {"runs", "reports", "generated", "private_artifacts"}:
            raise ValueError("Unexpected headless input: " + name)
        files["definitions/" + name] = content
        source_files[name] = {"sha256": digest(content), "size": len(content)}
        if name in {"LICENSE", "THIRD_PARTY_NOTICES.md"}:
            files[name] = content
    required = ("manifest.json", "harness_pins.json", "LICENSE", "THIRD_PARTY_NOTICES.md",
                "config/browser_driver_bindings.json")
    if any("definitions/" + name not in files for name in required):
        raise ValueError("Incomplete upstream headless data contract")
    manifest = json.loads(files["definitions/manifest.json"])
    bindings = json.loads(files["definitions/config/browser_driver_bindings.json"])
    datasets, used = {}, set()
    for layer in manifest["layers"]:
        for subset in layer["subsets"]:
            identity, pattern = subset["subset_id"], subset["task_glob"]
            if (not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", identity)
                    or identity in datasets or identity == "scenario-specs"
                    or not pattern.startswith("tasks/") or ".." in Path(pattern).parts
                    or "**" in pattern):
                raise ValueError("Unsafe or duplicate headless subset")
            names = sorted(name for name in files if name.startswith("definitions/tasks/")
                           and len(Path(name).parts[1:]) == len(Path(pattern).parts)
                           and all(fnmatchcase(value, glob) for value, glob in
                                   zip(Path(name).parts[1:], Path(pattern).parts)))
            if not names or used.intersection(names):
                raise ValueError("Missing or overlapping headless task population: " + identity)
            used.update(names)
            cases = [json.loads(files[name]) for name in names]
            path = "viewer/" + identity + ".json"
            payload = encoded({"cases": cases})
            files["definitions/" + path] = payload
            datasets[identity] = {"path": path, "sha256": digest(payload), "count": len(cases),
                                  "id_field": "task_id", "driver": subset["driver"],
                                  "layer": layer["layer_id"], "protocol": subset,
                                  "source_paths": {case["task_id"]: name.removeprefix("definitions/")
                                                   for case, name in zip(cases, names)}}
    scenarios = sorted(name for name in files if name.startswith("definitions/tasks/scenarios/")
                       and name.endswith(".json"))
    cases = [json.loads(files[name]) for name in scenarios]
    if not cases:
        raise ValueError("Missing headless scenario specifications")
    path, payload = "viewer/scenario-specs.json", encoded({"cases": cases})
    files["definitions/" + path] = payload
    datasets["scenario-specs"] = {"path": path, "sha256": digest(payload), "count": len(cases),
                                 "id_field": "scenario_id", "population": "scenario-specifications",
                                 "source_paths": {case["scenario_id"]: name.removeprefix("definitions/")
                                                  for case, name in zip(cases, scenarios)}}
    task_files = {name for name in files if name.startswith("definitions/tasks/") and name.endswith(".json")}
    if task_files != used | set(scenarios):
        raise ValueError("Unregistered headless task definitions")
    source = {"repository": HEADLESS_REPOSITORY, "revision": revision,
              "bench_id": manifest["bench_id"], "bench_version": manifest["bench_version"],
              "task_count": len(used), "scenario_count": len(scenarios), "subset_count": len(datasets) - 1,
              "fixture_count": sum(name.startswith("definitions/fixtures/") for name in files),
              "config_count": sum(name.startswith("definitions/config/") for name in files),
              "driver_count": len(bindings["driver_ids"]), "driver_ids": bindings["driver_ids"],
              "file_count": len(originals)}
    files["definitions/registry.json"] = encoded({"datasets": datasets, "source": source, "source_files": source_files})
    return files


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
    external_sources: dict | None = None
    scorer: dict | None = None
    default_dataset: str | None = None

    @classmethod
    def from_git(cls, checkout: Path, track: str, revision: str) -> DatasetRelease:
        registered = json.loads((CARDS / "metadata.json").read_bytes())
        if track not in registered:
            raise ValueError("Unknown benchmark track")
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("A release requires a full Git commit")
        kind = subprocess.check_output(["git", "cat-file", "-t", revision], cwd=checkout, text=True).strip()
        if kind != "commit":
            raise ValueError("A release requires a Git commit, not a tree or blob")
        hub = subprocess.run(["git", "cat-file", "-e", revision + ":definitions"],
                             cwd=checkout, capture_output=True).returncode == 0
        if track == "headless-browser" and not hub:
            return cls(track, revision, _headless_definitions(checkout, revision), HEADLESS_REPOSITORY)
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
        else:
            prefixes = [value for value in (f"src/lexbench_{track}/definitions/", f"lexbench_{track}/definitions/")
                        if subprocess.run(["git", "cat-file", "-e", revision + ":" + value.rstrip("/")],
                                          cwd=checkout, capture_output=True).returncode == 0]
            if len(prefixes) != 1:
                raise ValueError("Source requires one committed definitions tree")
            prefix = prefixes[0]
        notices = subprocess.check_output(
            ["git", "ls-tree", "--name-only", revision, "--", "LICENSE", "NOTICE",
             "THIRD_PARTY.md", "THIRD_PARTY_NOTICES.md"], cwd=checkout, text=True).splitlines()
        files = {}
        for original_name, content in _git_blobs(checkout, revision, (prefix, *notices)).items():
            name = "definitions/" + original_name[len(prefix):] if original_name.startswith(prefix) else original_name
            allowed = (track == "headless-browser" or Path(name).suffix in {".json", ".jsonl", ".txt", ".md"}
                       or Path(name).name in {"LICENSE", "NOTICE"}
                       or (track == "wcxb" and name.endswith(".html.gz")))
            if name.startswith("definitions/") and not allowed:
                raise ValueError("Unexpected definition file: " + name)
            pointer = re.fullmatch(rb"version https://git-lfs.github.com/spec/v1\noid sha256:([0-9a-f]{64})\nsize ([0-9]+)\n", content)
            if pointer:
                local = checkout / original_name
                if local.is_symlink() or not local.resolve().is_relative_to(checkout.resolve()):
                    raise ValueError("Unsafe local LFS file")
                content = local.read_bytes()
                if digest(content) != pointer[1].decode() or len(content) != int(pointer[2]):
                    raise ValueError("Materialize the exact committed LFS object: " + original_name)
            files[name] = content
        repository = "https://huggingface.co/datasets/" + registered[track]["repo_id"] if hub else None
        settings = identity if hub else json.loads(files['definitions/registry.json'])
        return cls(track, revision, files, repository,
                   external_sources=identity.get("external_sources") if hub else None,
                   scorer=settings.get('scorer'), default_dataset=settings.get('default_dataset'))

    def runtime_settings(self) -> dict:
        """Publish the execution choices bound to this dataset edition."""
        result = {}
        if self.default_dataset is not None:
            if not isinstance(self.default_dataset, str) or self.default_dataset not in self.datasets():
                raise ValueError('Default dataset is absent from release')
            result['default_dataset'] = self.default_dataset
        if self.scorer is not None:
            if (not isinstance(self.scorer, dict)
                    or not isinstance(self.scorer.get('repository'), str)
                    or not self.scorer['repository'].strip()
                    or not isinstance(self.scorer.get('revision'), str)
                    or not re.fullmatch(r'[0-9a-f]{40}', self.scorer['revision'])):
                raise ValueError('Scorer requires a repository and full Git commit')
            result['scorer'] = self.scorer
        return result

    def datasets(self) -> dict[str, dict]:
        registry = json.loads(self.files["definitions/registry.json"])
        if self.track == "webfetch":
            datasets = {}
            for row in registry["suites"]:
                directory, declared = row["directory"], row["files"]
                if (not isinstance(directory, str) or Path(directory).is_absolute()
                        or ".." in Path(directory).parts or not isinstance(declared, dict)
                        or "tasks.json" not in declared or row["id"] in datasets):
                    raise ValueError("Invalid Web Fetch suite file contract")
                for relative, expected in declared.items():
                    if (not isinstance(relative, str) or not relative or Path(relative).is_absolute()
                            or ".." in Path(relative).parts or not isinstance(expected, str)
                            or not re.fullmatch(r"[0-9a-f]{64}", expected)):
                        raise ValueError("Invalid Web Fetch definition binding")
                    name = "definitions/" + (Path(directory) / relative).as_posix()
                    if name not in self.files or digest(self.files[name]) != expected:
                        raise ValueError("Web Fetch definition missing or changed: " + name)
                datasets[row["id"]] = {
                    "path": (Path(directory) / "tasks.json").as_posix(),
                    "sha256": declared["tasks.json"], "count": row["case_count"],
                }
            return datasets
        return registry["datasets"]

    def external_bindings(self, selections: dict | None = None) -> dict:
        """Keep each delegated input attached to its published provider commit."""
        metadata = json.loads((CARDS / "metadata.json").read_bytes())[self.track]
        datasets = self.datasets()
        context = selections if selections is not None else self.external_sources
        if context is not None and not isinstance(context, dict):
            raise ValueError("External sources must be a provider mapping")
        result = {}
        for identity, composition in metadata.get("external_datasets", {}).items():
            if identity not in datasets:
                continue
            provider = composition["source"]
            descriptor = datasets[identity]
            if (not isinstance(descriptor, dict) or not isinstance(descriptor.get("path"), str)
                    or not isinstance(descriptor.get("sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", descriptor["sha256"])
                    or type(descriptor.get("count")) is not int or descriptor["count"] <= 0):
                raise ValueError("External dataset descriptor is invalid: " + identity)
            paths = [descriptor["path"], *composition.get("resources", [])]
            if context is None:
                if any("definitions/" + path not in self.files for path in paths):
                    raise ValueError("External source requires a recorded immutable binding or explicit selections: " + provider)
                from harness.benchmarks.data import binding as selected_binding
                source = selected_binding(provider)
            else:
                source = context.get(provider, {})
            supplied = (source["datasets"].get(identity) if isinstance(source, dict)
                        and isinstance(source.get("datasets"), dict) else None)
            if (not isinstance(supplied, dict) or type(supplied.get("count")) is not int
                    or supplied != descriptor):
                raise ValueError("External dataset descriptor differs: " + identity)
            if (not isinstance(source.get("repository"), str)
                    or not re.fullmatch(r"[\w.-]+/[\w.-]+", source["repository"])
                    or not isinstance(source.get("revision"), str)
                    or not re.fullmatch(r"[0-9a-f]{40}", source["revision"])
                    or not isinstance(source.get("files"), dict)):
                raise ValueError("External source requires an immutable repository and revision: " + provider)
            selected = result.setdefault(provider, {"repository": source["repository"],
                "revision": source["revision"], "datasets": {}, "files": {}})
            selected["datasets"][identity] = datasets[identity]
            for relative in paths:
                if (not isinstance(relative, str) or not relative or Path(relative).is_absolute()
                        or ".." in Path(relative).parts):
                    raise ValueError("External definition path is unsafe")
                name = "definitions/" + relative
                expected = source["files"].get(name)
                if (not isinstance(expected, dict) or not isinstance(expected.get("sha256"), str)
                        or not re.fullmatch(r"[0-9a-f]{64}", expected["sha256"])
                        or type(expected.get("size")) is not int or expected["size"] < 0
                        or (relative == datasets[identity]["path"] and expected["sha256"] != datasets[identity]["sha256"])
                        or (name in self.files and (digest(self.files[name]) != expected["sha256"]
                            or len(self.files[name]) != expected["size"]))):
                    raise ValueError("External definition differs: " + name)
                selected["files"][name] = {"sha256": expected["sha256"], "size": expected["size"]}
        return result

    def external_definitions(self, selections: dict | None = None) -> dict[str, str]:
        return {name: provider for provider, selected in self.external_bindings(selections).items()
                for name in selected["files"]}

    def viewer_datasets(self) -> set[str]:
        """Select current populations without changing historical definitions."""
        metadata = json.loads((CARDS / "metadata.json").read_bytes())[self.track]
        registry = json.loads(self.files["definitions/registry.json"])
        datasets = self.datasets()
        selection = metadata.get("viewer_selection")
        selected = set(datasets)
        if selection and (registry.get("schema") is not None or "current" in registry):
            if registry.get("schema") != selection["schema"]:
                raise ValueError("Unknown viewer registry schema")
            if "current" in selection:
                current = registry.get("current", {})
                if any(key not in current for key in selection["current"]):
                    raise ValueError("Missing current viewer selection")
                selected = {current[key] for key in selection["current"]}
            else:
                selected = set(selection["datasets"])
            if not selected or not selected.issubset(datasets):
                raise ValueError("Unknown current viewer dataset")
        selected -= set(metadata.get("external_datasets", {}))
        selected -= set(metadata.get("viewer_exclude_datasets", []))
        return selected

    def build(self, output: Path, *, selections: dict | None = None) -> dict:
        if output.exists() and any(output.iterdir()):
            raise ValueError("Release output must be empty")
        if self.tooling_revision is not None and not re.fullmatch(r"[0-9a-f]{40}", self.tooling_revision):
            raise ValueError("Tooling requires a full Git commit")
        import yaml

        body = (self.card_body if self.card_body is not None
                else (CARDS / (self.track + ".md")).read_text(encoding="utf-8"))
        settings = self.runtime_settings()
        context = self.external_bindings(selections)
        external = self.external_definitions(context)
        definition_files = {name: raw for name, raw in self.files.items() if name not in external}
        files = dict(definition_files)
        if self.track == "headless-browser":
            registry = json.loads(files["definitions/registry.json"])
            for name, binding in registry["source_files"].items():
                content = files["definitions/" + name]
                if digest(content) != binding["sha256"] or len(content) != binding["size"]:
                    raise ValueError("Original headless input does not match inventory: " + name)
        configurations = []
        summary = []
        languages = set()
        used_annotations = {}
        metadata = json.loads((CARDS / "metadata.json").read_bytes())[self.track]
        annotations = self.language_annotations
        if annotations is None:
            annotations = json.loads((CARDS / "languages.json").read_bytes()).get(self.track, {})
        for identity, binding, cases in self.records(context):
            identity_field = binding.get("id_field", "id")
            groups = {}
            partition = metadata.get("partitions", {}).get(identity)
            for case in cases:
                if case.get('track') in metadata.get('viewer_exclude_tracks', {}).get(identity, []):
                    continue
                case_id = case[identity_field]
                annotation = annotations.get(identity, {}).get(case_id)
                if annotation is not None:
                    if digest(encoded(case)) != annotation["case_sha256"]:
                        raise ValueError("Language annotation does not match frozen task: " + case_id)
                    language = annotation["language"]
                    used_annotations.setdefault(identity, {})[case_id] = annotation
                    languages.update(annotation.get("languages", [language]))
                else:
                    language = metadata.get("default_language", "und")
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
                if self.track == 'webfetch':
                    from harness.web_fetch.models import report_family
                    row['benchmark_category'] = report_family(case)
                if "source_paths" in binding:
                    row["source_path"] = binding["source_paths"][case_id]
                    row.update({key: binding[key] for key in ("layer", "driver") if key in binding})
                category_field = metadata.get("category_field")
                if "category" not in row and category_field and case.get(category_field):
                    row["category"] = case[category_field]
                name = identity
                if partition:
                    value = row.get(partition['field'], case.get(partition['field']))
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
        manifest.update(settings)
        if external:
            manifest["external_sources"] = context
            manifest["dataset_sources"] = {
                identity: composition["source"] for identity, composition in
                metadata.get("external_datasets", {}).items() if identity in self.datasets()
            }
        if self.tooling_revision is not None:
            manifest["tooling"] = {"repository": "https://github.com/lexmount/browser-eval",
                                   "revision": self.tooling_revision}
        if self.track == "headless-browser":
            manifest["upstream"] = json.loads(self.files["definitions/registry.json"])["source"]
        files["manifest.json"] = encoded(manifest)
        output.mkdir(parents=True, exist_ok=True)
        for name, raw in files.items():
            path = output / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        return self._finish(output, manifest, configurations, metadata, languages, body, yaml, definition_files)

    def records(self, selections: dict | None = None):
        datasets = self.datasets()
        if not isinstance(datasets, dict) or not datasets:
            raise ValueError("A release requires registered datasets")
        external = self.external_definitions(selections)
        selected = self.viewer_datasets()
        for identity, binding in datasets.items():
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", identity):
                raise ValueError("Unsafe dataset identity")
            name = "definitions/" + binding["path"]
            if name in external:
                continue
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
            if "source_paths" in binding:
                if set(binding["source_paths"]) != set(ids):
                    raise ValueError("Source path population mismatch: " + identity)
                for case in cases:
                    original = self.files["definitions/" + binding["source_paths"][case[identity_field]]]
                    if json.loads(original) != case:
                        raise ValueError("Viewer projection differs from original task: " + identity)
            if identity in selected:
                yield identity, binding, cases

    def _finish(self, output: Path, manifest: dict, configurations: list[dict], metadata: dict,
                languages: set[str], body: str, yaml, definition_files: dict[str, bytes]) -> dict:

        if self.track == 'webfetch':
            order = {name: index for index, name in enumerate(metadata['subsets'])}
            configurations.sort(key=lambda row: order.get(row['config_name'], len(order)))
            manifest['viewer']['configurations'].sort(
                key=lambda row: order.get(row['config'], len(order)))
        buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0, filename="") as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for name, raw in sorted(definition_files.items()):
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
        if self.track == "headless-browser":
            for key, value in manifest["upstream"].items():
                if isinstance(value, (str, int)):
                    replacement = f"{value:,}" if isinstance(value, int) else value
                    body = body.replace("{{upstream_" + key + "}}", replacement)
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
    parser.add_argument("--publish", metavar="HF_REPOSITORY", help="Open a Hugging Face pull request for the built dataset")
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
                                   create_pr=True,
                                   commit_message="Publish verified " + args.track + " definitions")
        print(json.dumps({"repository": args.publish, "revision": commit.oid,
                          "pull_request": commit.pr_url, "state": "review_pending"}))


if __name__ == "__main__":
    main()
