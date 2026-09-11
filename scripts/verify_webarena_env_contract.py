"""Verify that webarena-env and browser-eval bind the same site images."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "evaluation/browser-use-public/webarena-env-contract.json"


def verify(environment_repo: Path, contract_path: Path = DEFAULT_CONTRACT) -> dict:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract.get("schema") != "lexmount_webarena_cross_repo_contract/1":
        raise ValueError("invalid cross-repository contract schema")
    env_lock = json.loads(
        (environment_repo.resolve() / contract["environment_lock"]).read_text(encoding="utf-8")
    )
    eval_lock = json.loads(
        (ROOT / contract["evaluation_lock"]).read_text(encoding="utf-8")
    )
    expected_version = contract["webarena_verified_version"]
    if env_lock.get("webarena_verified_version") != expected_version:
        raise ValueError("webarena-env package version binding differs")
    if eval_lock.get("webarena_verified_version") != expected_version:
        raise ValueError("browser-eval package version binding differs")
    sites = contract["required_sites"]
    if set(env_lock.get("images") or {}) != set(sites):
        raise ValueError("webarena-env image inventory differs")
    if set(eval_lock.get("images") or {}) != set(sites):
        raise ValueError("browser-eval image inventory differs")
    mismatches = []
    for site in sites:
        left = env_lock["images"][site]
        right = eval_lock["images"][site]
        for key in ("pinned", "architecture", "os"):
            if left.get(key) != right.get(key):
                mismatches.append(f"{site}.{key}")
    if mismatches:
        raise ValueError("cross-repository image binding differs: " + ", ".join(mismatches))
    return {
        "status": "pass",
        "webarena_verified_version": expected_version,
        "sites": len(sites),
        "environment_pull_request": contract["environment_pull_request"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment-repo", type=Path, required=True)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    args = parser.parse_args()
    print(json.dumps(verify(args.environment_repo, args.contract), ensure_ascii=False))


if __name__ == "__main__":
    main()
