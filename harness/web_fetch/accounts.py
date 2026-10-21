from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path
from typing import Any


from .methods import report_method_catalog


def account_checklist() -> list[dict[str, Any]]:
    """Hosted setup candidates; planned products remain explicitly marked."""
    return [dict(access, id=name, provider=method['label']['zh'],
                 implemented=method.get('status') == 'implemented')
            for name, method in report_method_catalog()['methods'].items()
            if (access := method.get('access', {})).get('priority')]


def credential_state(item: dict[str, Any]) -> str:
    present = [bool(os.getenv(name, "").strip()) for name in item["env_vars"]]
    if all(present):
        return "PRESENT"
    if not item["required"]:
        return "OPTIONAL"
    return "MISSING"


def env_template() -> str:
    names: list[str] = []
    for item in account_checklist():
        for name in item["env_vars"]:
            if name not in names:
                names.append(name)
    return "\n".join(f"{name}=" for name in names) + "\n"


def print_account_checklist() -> None:
    print("WEB FETCH ACCOUNT CHECKLIST")
    print("=" * 72)
    for item in account_checklist():
        state = credential_state(item)
        support = "适配器已实现" if item["implemented"] else "适配器待实现"
        print(f"[{state}] {item['provider']} — {item['priority']} — {support}")
        for label, url in item["links"]:
            print(f"  {label}：{url}")
        print(f"  环境变量：{', '.join(item['env_vars'])}")
        print(f"  获取步骤：{item['configuration']}")
        print(f"  准入说明：{item['account']}")
        print()
    print("COPY INTO .env AND FILL VALUES")
    print("-" * 72)
    print(env_template(), end="")


HOSTED_KEYS = tuple(dict.fromkeys(
    key for account in account_checklist()
    if account.get("implemented") and account.get("required")
    for key in account.get("env_vars", [])
))

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


def main_import_credentials(argv: list[str] | None = None, *, default_target: Path | None = None) -> None:
    parser = argparse.ArgumentParser(description="Import declared hosted credentials into a .env file.")
    parser.add_argument("--source", type=Path, default=Path.home() / ".env")
    parser.add_argument("--target", type=Path, default=default_target or Path.cwd() / ".env")
    args = parser.parse_args(argv)
    imported, missing = import_credentials(args.source, args.target)
    print(f"Imported {len(imported)} hosted benchmark variables into {args.target}")
    if missing:
        raise SystemExit("Missing or empty source variables: " + ", ".join(missing))
    print(f"Hosted benchmark credentials complete: {len(HOSTED_KEYS)}/{len(HOSTED_KEYS)}")
