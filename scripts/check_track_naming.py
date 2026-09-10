#!/usr/bin/env python3
"""Reject retired track names from maintained repository files."""

from __future__ import annotations

from pathlib import Path


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
TEXT_SUFFIXES = {"", ".css", ".html", ".js", ".json", ".md", ".py", ".sh", ".toml", ".txt", ".yaml", ".yml"}
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


def main() -> None:
    findings: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        relative = path.relative_to(ROOT)
        name = relative.as_posix()
        if EXCLUDED_PARTS.intersection(relative.parts):
            continue
        if any(name == root or name.startswith(root + "/") for root in EXCLUDED_ROOTS):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for term in FORBIDDEN:
            if term in text:
                findings.append(f"{name}: retired track name {term!r}")
    if findings:
        raise SystemExit("\n".join(findings))
    print("Track naming check passed")


if __name__ == "__main__":
    main()
