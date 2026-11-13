"""Deterministic synthetic success/failure report; never measured evidence."""

import argparse
import json
from pathlib import Path

from harness.record_io import bytes_sha256
from harness.web_search.core import normalize, write_json
from harness.web_search.relevance import import_annotations
from harness.web_search.report import build
from lexbench_websearch.relevance import candidate_packet
from lexbench_websearch.scoring import evaluate


def synthetic_profile():
    """A small execution fixture with no published instance or measured results."""
    return {
        "schema": "web-search/1", "title": "Synthetic rendering fixture — not measurements",
        "protocol": {"top_k": 5, "rounds": 1, "score_round": 0, "concurrency": 1, "server_timeout_ms": 1000, "client_timeout_s": 2, "interval_s": 0, "retry": False, "review": "Synthetic fixture without independent review"},
        "methods": [
            {"id": "lexmount", "label": "Fixture A", "adapter": "lexmount", "endpoint": "https://fixture-a.invalid/search", "key_env": "FIXTURE_A_KEY", "currency": "CNY", "price_per_1000": None, "billing": "unknown"},
            {"id": "deepseek-content", "label": "Fixture B", "adapter": "deepseek", "endpoint": "https://fixture-b.invalid/search", "key_env": "FIXTURE_B_KEY", "currency": "CNY", "price_per_1000": 5, "billing": "http-200-nonempty", "output_options": {"need_content": False}},
        ],
        "cases": [{"id": "fixture-" + str(i), "family": "web", "language": "zh", "query": "Synthetic query " + str(i), "anchors": ["https://reference.invalid/" + str(i)], "origin": "Synthetic fixture", "provenance_kind": "curated"} for i in range(2)],
    }


def example(output, *, comparison=False, levels=False):
    profile = synthetic_profile()
    if levels:
        comparison = True
        profile["methods"] = profile["methods"][:1]
    if comparison:
        profile['cases'][1].update(reference_answer='Fixture only',
                                   reference_urls=profile['cases'][1]['anchors'])
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
                    evidence=dict(path=path, sha256=bytes_sha256((source / path).read_bytes())),
                    metrics=evaluate(case, results),
                    server_timing=payload.get("timing"),
                )
            )
    (source / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    write_json(
        source / "receipt.json",
        dict(
            complete=True,
            certified=False,
            synthetic=True,
            call_count=len(rows),
            profile_sha256=bytes_sha256((source / "profile.json").read_bytes()),
            results_sha256=bytes_sha256((source / "results.jsonl").read_bytes()),
        ),
    )
    if not comparison:
        return build(output / "report", source=source)
    label_profile, label_rows = profile, rows
    if levels:
        from harness.web_search.output_levels import level_inputs

        label_profile, label_rows = level_inputs(profile, rows, 'l1')
    packet, _ = candidate_packet(label_profile, label_rows)
    labels = import_annotations(label_profile, label_rows, {
        'packet_sha256': packet['packet_sha256'], 'reviewed_case_ids': packet['plan']['case_ids'],
        'judge': {'kind': 'human', 'name': 'Synthetic fixture labels; not a human review'},
        'labels': {p['case_id']: {c['candidate_id']: 3 for c in p['candidates']}
                   for p in packet['blind_candidate_packets']},
    })
    content = {
        'presentation': 'comparison-report', 'independent_benchmarks': True,
        'summary_metrics': 'retrieval', 'sections': [],
        'metric_populations': [
            {'id': 'custom', 'metric_kind': 'relevance', 'benchmark_root': True,
             'case_ids': [profile['cases'][0]['id']],
             'label': {'en': 'Synthetic scenarios', 'zh': 'Synthetic scenarios'}},
            {'id': 'factual', 'benchmark_root': True, 'case_ids': [profile['cases'][1]['id']],
             'label': {'en': 'Synthetic factual queries', 'zh': 'Synthetic factual queries'}},
        ],
    }
    if levels:
        write_json(output/'level-l1.json', labels)
        write_json(output/'level-assessments.json', dict(schema='web-search-level-assessments/1',
                   relevance={'l1': dict(path='level-l1.json', sha256=bytes_sha256((output/'level-l1.json').read_bytes()))}))
        content['output_level_assessments'] = dict(path='level-assessments.json',
                                                sha256=bytes_sha256((output/'level-assessments.json').read_bytes()))
    write_json(output / 'content.json', content)
    write_json(output / 'labels.json', labels)
    return build(output / 'report', source=source, content=output / 'content.json',
                 relevance=None if levels else output / 'labels.json', include_english=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument('--comparison', action='store_true', help='Build the two-benchmark comparison')
    parser.add_argument('--levels', action='store_true', help='Build an isolated output-level report')
    args = parser.parse_args()
    print(example(args.output, comparison=args.comparison, levels=args.levels))


if __name__ == "__main__":
    main()
