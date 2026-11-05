#!/usr/bin/env python3
"""Reject retired track names from maintained repository files."""

from __future__ import annotations

import json
import re
from pathlib import Path

from harness.distribution import source_path
from harness.web_fetch.reference_inputs import check_reference_input_boundary

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_ROOTS = {
    ".git",
    "node_modules",
    "publish",
    "reports",
    "evaluation/dataset.json",
    "evaluation/sources",
}
EXCLUDED_PARTS = {".pytest_cache", ".ruff_cache", ".venv", "__pycache__"}
TEXT_SUFFIXES = {
    "",
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
FORBIDDEN = (
    "browser_" + "agent",
    "browser-" + "agent",
    "Browser " + "Agent",
    "browser_" + "kernel",
    "browser-" + "kernel",
    "Browser " + "Kernel",
    "Browser-" + "agent",
    "Browser-" + "kernel",
)


_EXTRACT_RETIRED = re.compile(
    r"\bweb[-_]" + r"scraper\b|\bscraper-(?:theme|(?:cohort-|record-)?example)\b",
    re.IGNORECASE,
)


def extract_name_findings(name: str, text: str) -> list[str]:
    """Keep current names canonical while preserving original source identities."""
    if _EXTRACT_RETIRED.search(name.replace("_", "-")):
        return [f"{name}: use the current Web Extract namespace"]
    if name.startswith(("research/", "evaluation/")):
        return []
    scanned = text
    if name == "src/harness/benchmarks/data-lock.json":
        value = json.loads(text)
        # Historical release objects retain original repository identities and hashes.
        for selection in value.get("datasets", {}).values():
            selection.pop("history", None)
        scanned = json.dumps(value)
    elif name == "src/harness/benchmarks/code-migration.json":
        value = json.loads(text)
        value.pop("legacy_names", None)
        scanned = json.dumps(value)
    # URLs identify external products or pinned source revisions, not our namespace.
    scanned = re.sub(r"https?://[^\s\"'<>]+", "", scanned)
    if _EXTRACT_RETIRED.search(scanned):
        return [f"{name}: use the current Web Extract namespace"]
    return []


def repository_contract_findings(root: Path) -> list[str]:
    findings: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        relative = path.relative_to(root)
        name = relative.as_posix()
        if EXCLUDED_PARTS.intersection(relative.parts) or any(
            part.endswith(".egg-info") for part in relative.parts
        ):
            continue
        if any(name == root or name.startswith(root + "/") for root in EXCLUDED_ROOTS):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        findings.extend(extract_name_findings(name, text))
        for term in FORBIDDEN:
            if term in text:
                findings.append(f"{name}: retired track name {term!r}")
    catalog_replay_scripts = sorted(
        path.relative_to(root).as_posix()
        for path in (root / "scripts").glob("*webarena_catalog*.py")
    )
    if catalog_replay_scripts:
        findings.append(
            "WebArena catalog stages are harness.trajectory_replay subcommands, not scripts: "
            + ", ".join(catalog_replay_scripts)
        )
    specialized_replay_entrypoints = sorted(
        path.relative_to(root).as_posix() for path in (root / "scripts").glob("*webarena*full*.py")
    )
    if specialized_replay_entrypoints:
        findings.append(
            "dataset size belongs in catalog data, not specialized entry points: "
            + ", ".join(specialized_replay_entrypoints)
        )
    specialized_catalog_modules = sorted(
        path.relative_to(root).as_posix()
        for path in (source_path("harness", root=root) / "trajectory_replay").glob(
            "*full*catalog*.py"
        )
    )
    if specialized_catalog_modules:
        findings.append(
            "catalog contracts belong in the shared pipeline, not full-dataset modules: "
            + ", ".join(specialized_catalog_modules)
        )
    contracts_path = (
        source_path("harness", root=root) / "trajectory_replay" / "catalog_contracts.py"
    )
    schema_implementation_paths = [
        *(source_path("harness", root=root) / "trajectory_replay").glob("*.py"),
        *(root / "scripts").glob("*.py"),
    ]
    legacy_schema_prefixes = (
        "lex_webarena_" + "full_",
        "lex_webarena_verified_" + "full_",
    )
    for path in schema_implementation_paths:
        if path == contracts_path:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(prefix in text for prefix in legacy_schema_prefixes):
            findings.append(
                f"{path.relative_to(root).as_posix()}: legacy full schemas are read-only contracts"
            )
    makefile = root / "Makefile"
    if makefile.is_file() and re.search(
        r"(?m)^[A-Za-z0-9_.-]*webarena-full[A-Za-z0-9_.-]*\s*:",
        makefile.read_text(encoding="utf-8"),
    ):
        findings.append("Makefile: catalog release targets must not encode one dataset size")
    repair_programs = sorted(
        path.relative_to(root).as_posix()
        for path in (root / "scripts").glob("repair_webarena_*.py")
    )
    if repair_programs:
        findings.append(
            "case repair belongs in catalog data and the shared replay pipeline, not programs: "
            + ", ".join(repair_programs)
        )
    return findings


def main() -> None:
    check_reference_input_boundary(ROOT / "evaluation")
    findings = repository_contract_findings(ROOT)
    if findings:
        raise SystemExit("\n".join(findings))
    print("Track contract checks passed")


if __name__ == "__main__":
    main()
