#!/usr/bin/env python3
"""Import only declared hosted-benchmark credentials into the repo .env."""
from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOSTED_KEYS = (
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID",
    "LEXMOUNT_API_KEY",
    "LEXMOUNT_PROJECT_ID",
    "LEXMOUNT_CN_API_KEY",
    "LEXMOUNT_CN_PROJECT_ID",
    "FIRECRAWL_API_KEY",
    "TAVILY_API_KEY",
    "EXA_API_KEY",
)
ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$"
)


def assignments(path: Path) -> dict[str, str]:
    """Return first raw dotenv value for each key without evaluating it."""
    values: dict[str, str] = {}
    for line in path.expanduser().read_text(encoding="utf-8").splitlines():
        match = ASSIGNMENT.fullmatch(line)
        if match:
            values.setdefault(match.group(1), match.group(2).strip())
    return values


def usable(raw: str | None) -> bool:
    if raw is None:
        return False
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return bool(value)


def import_credentials(source: Path, target: Path) -> tuple[list[str], list[str]]:
    source = source.expanduser().resolve()
    target = target.expanduser().resolve()
    if source == target:
        raise ValueError("Source and target env files must differ")
    source_values = assignments(source)
    target_values = assignments(target) if target.exists() else {}
    imported: list[str] = []
    missing: list[str] = []
    additions: list[str] = []
    for key in HOSTED_KEYS:
        if usable(target_values.get(key)):
            continue
        raw = source_values.get(key)
        if not usable(raw):
            missing.append(key)
            continue
        additions.append(f"{key}={raw}")
        imported.append(key)
    if additions:
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        if existing and not existing.endswith("\n"):
            existing += "\n"
        payload = existing + "\n".join(additions) + "\n"
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".env-import-", dir=target.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    elif target.exists():
        os.chmod(target, 0o600)
    return imported, missing


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path.home() / ".env")
    parser.add_argument("--target", type=Path, default=ROOT / ".env")
    args = parser.parse_args(argv)
    imported, missing = import_credentials(args.source, args.target)
    print(f"Imported {len(imported)} hosted benchmark variables into {args.target}")
    if missing:
        raise SystemExit("Missing or empty source variables: " + ", ".join(missing))
    print(f"Hosted benchmark credentials complete: {len(HOSTED_KEYS)}/{len(HOSTED_KEYS)}")


if __name__ == "__main__":
    main()
