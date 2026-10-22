"""Deterministic synthetic success/failure report; never measured evidence."""

import argparse
import json
from pathlib import Path

from .core import digest, evaluate, normalize, write_json
from .report import build


def example(output):
    profile = {
        "schema": "web-search/1", "title": "Synthetic rendering fixture — not measurements",
        "protocol": {"top_k": 5, "rounds": 1, "score_round": 0, "concurrency": 1, "server_timeout_ms": 1000, "client_timeout_s": 2, "interval_s": 0, "retry": False, "review": "Synthetic fixture without independent review"},
        "methods": [
            {"id": "lexmount", "label": "Fixture A", "adapter": "lexmount", "endpoint": "https://fixture-a.invalid/search", "key_env": "FIXTURE_A_KEY", "currency": "CNY", "price_per_1000": None, "billing": "unknown"},
            {"id": "deepseek-content", "label": "Fixture B", "adapter": "deepseek", "endpoint": "https://fixture-b.invalid/search", "key_env": "FIXTURE_B_KEY", "currency": "CNY", "price_per_1000": 5, "billing": "http-200-nonempty", "output_options": {"need_content": False}},
        ],
        "cases": [{"id": "fixture-" + str(i), "family": "web", "language": "zh", "query": "Synthetic query " + str(i), "anchors": ["https://reference.invalid/" + str(i)], "origin": "Synthetic fixture", "provenance_kind": "curated"} for i in range(2)],
    }
    source = output / "fixture"
    source.mkdir(parents=True, exist_ok=True)
    write_json(source / "profile.json", profile)
    rows = []
    for ci, case in enumerate(profile["cases"]):
        for mi, method in enumerate(profile["methods"]):
            failed = ci == mi == 1
            payload = (
                {"error": "Synthetic upstream timeout"}
                if failed
                else {
                    "timing": {"elapsed_ms": 80},
                    "attempts": [
                        {
                            "engine": "synthetic",
                            "route": "fixture",
                            "started_after_ms": 0,
                            "elapsed_ms": 50,
                            "outcome": "results",
                            "parsed_count": 1,
                        }
                    ],
                    "results": [
                        dict(
                            title="Synthetic reference",
                            url=case["anchors"][0],
                            snippet="Fixture only",
                        )
                    ],
                }
            )
            identity = f"{case['id']}-{method['id']}-r0"
            path = "calls/" + identity + ".json"
            write_json(source / path, payload)
            results = [] if failed else normalize(payload, method["adapter"])
            rows.append(
                dict(
                    id=identity,
                    case_id=case["id"],
                    method=method["id"],
                    round=0,
                    elapsed_ms=100 + 100 * mi,
                    http_status=504 if failed else 200,
                    error="Synthetic upstream timeout" if failed else "",
                    results=results,
                    cost=None if mi == 0 else (0 if failed else 0.005),
                    currency="CNY",
                    evidence=dict(path=path, sha256=digest((source / path).read_bytes())),
                    metrics=evaluate(case, results),
                    server_timing=payload.get("timing"),
                )
            )
    (source / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    write_json(
        source / "receipt.json",
        dict(
            complete=True,
            certified=False,
            synthetic=True,
            call_count=len(rows),
            profile_sha256=digest((source / "profile.json").read_bytes()),
            results_sha256=digest((source / "results.jsonl").read_bytes()),
        ),
    )
    return build(output / "report", source=source)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(example(parser.parse_args().output))


if __name__ == "__main__":
    main()
