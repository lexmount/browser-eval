"""Build fixed browser paths for unresolved successful navigation contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.public_webarena.suite import load_suite

SOURCE = "https://github.com/lexmount/webarena-verified/blob/7d060ec92b611d66b48d667acb9d92c35cfbdbda/assets/dataset/webarena-verified.json"
PLACEHOLDERS = {
    "__SHOPPING__": "shopping",
    "__SHOPPING_ADMIN__": "shopping_admin",
    "__REDDIT__": "reddit",
    "__GITLAB__": "gitlab",
    "__MAP__": "map",
    "__WIKIPEDIA__": "wikipedia",
}


def _bound_url(value: str, sites: dict[str, str]) -> str:
    for placeholder, site in PLACEHOLDERS.items():
        if value.startswith(placeholder):
            return sites[site].rstrip("/") + value.removeprefix(placeholder)
    raise ValueError(f"navigation URL has no bound WebArena site: {value!r}")


def _navigation_urls(row: dict, sites: dict[str, str]) -> list[str] | None:
    evaluations = row["eval"][1:]
    # GitLab issue filters are rendered by a GraphQL request. Visiting its
    # declared referer produces both the page navigation and the API event.
    for evaluation in evaluations:
        expected = evaluation["expected"]
        if expected.get("http_method") == "POST":
            referer = (expected.get("headers") or {}).get("referer")
            if isinstance(referer, list):
                referer = referer[0]
            if isinstance(referer, str):
                return [_bound_url(referer, sites)]
            return None
    urls: list[str] = []
    for evaluation in evaluations:
        value = evaluation["expected"].get("url")
        if isinstance(value, list):
            value = value[0]
        if not isinstance(value, str) or any(character in value for character in "^*$"):
            return None
        urls.append(_bound_url(value, sites))
    return urls or None


def build_navigation_suite(
    official_path: Path, ledger_path: Path, sites_path: Path
) -> dict:
    official = json.loads(official_path.read_text(encoding="utf-8"))
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    sites = json.loads(sites_path.read_text(encoding="utf-8"))["sites"]
    unresolved = {
        row["task_id"] for row in ledger["rows"] if row.get("status") != "pass"
    }
    tasks = []
    for row in official:
        expected = row["eval"][0]["expected"]
        if (
            row["task_id"] not in unresolved
            or expected.get("task_type") != "navigate"
            or expected.get("status") != "SUCCESS"
        ):
            continue
        urls = _navigation_urls(row, sites)
        if urls is None:
            continue
        actions = [
            {"action": "navigate", "url": url, "save_as": f"page_{index}"}
            for index, url in enumerate(urls, 1)
        ]
        tasks.append(
            {
                "id": f"webarena-{row['task_id']}",
                "title": row["intent"],
                "trajectory": {
                    "schema": "lex_browser_trajectory/1",
                    "actions": actions,
                    "provenance": {
                        "kind": "official_navigation_contract_fixed_path"
                    },
                },
                "difficulty": "simple",
                "ground_truth": {
                    "oracle": "webarena_verified",
                    "expected_actions": ["navigate"] * len(actions),
                    "response_builder": {
                        "kind": "null_after_verified_path",
                        "task_type": "navigate",
                    },
                },
                "evaluator": {
                    "kind": "webarena_verified",
                    "task_id": row["task_id"],
                    "revision": row["revision"],
                },
                "source": {"url": SOURCE},
                "environment": {"kind": "webarena_verified"},
            }
        )
    if not tasks:
        raise ValueError("no unresolved successful navigation contracts were buildable")
    return {"schema": "lex_browser_use_tasks/4", "tasks": tasks}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official-dataset", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_navigation_suite(
        args.official_dataset, args.ledger, args.site_map
    )
    if args.output.exists():
        raise ValueError("navigation repair suite output already exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"tasks": len(load_suite(args.output).tasks)}))


if __name__ == "__main__":
    main()
