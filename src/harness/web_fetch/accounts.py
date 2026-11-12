from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path
from typing import Any

from harness.env_file import parse_env_file, read_env_statements
from harness.web_fetch import config
from harness.web_fetch.methods import report_method_catalog


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


def usable(value: str | None) -> bool:
    return bool(value and value.strip())


def import_credentials(
    source: Path, target: Path, *, keys: tuple[str, ...] = HOSTED_KEYS
) -> tuple[list[str], list[str]]:
    source = source.expanduser().resolve()
    target = target.expanduser().resolve()
    if source == target:
        raise ValueError("Source and target env files must differ")
    source_values = parse_env_file(source)
    source_statements = read_env_statements(source)
    target_values = parse_env_file(target) if target.exists() else {}
    imported: list[str] = []
    missing: list[str] = []
    additions: list[str] = []
    for key in keys:
        if usable(target_values.get(key)):
            continue
        if not usable(source_values.get(key)):
            missing.append(key)
            continue
        additions.append(source_statements[key])
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
            # An unterminated quote left in the target can absorb an appended assignment.
            readable = parse_env_file(temporary)
            if not all(usable(readable.get(key)) for key in imported):
                raise ValueError(f"Fix the unterminated quote in {target} before importing")
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    elif target.exists():
        os.chmod(target, 0o600)
    return imported, missing


def main_import_credentials(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Import declared hosted credentials into a .env file."
    )
    parser.add_argument("--source", type=Path, default=Path.home() / ".env")
    parser.add_argument("--target", type=Path, default=config.ROOT / ".env",
                        help="defaults to the repository .env that evaluation runs load")
    args = parser.parse_args(argv)
    imported, missing = import_credentials(args.source, args.target)
    print(f"Imported {len(imported)} hosted benchmark variables into {args.target}")
    if missing:
        raise SystemExit("Missing or empty source variables: " + ", ".join(missing))
    print(f"Hosted benchmark credentials complete: {len(HOSTED_KEYS)}/{len(HOSTED_KEYS)}")
