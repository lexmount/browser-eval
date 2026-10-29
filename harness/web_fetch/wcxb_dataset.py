"""Freeze a type-stratified WCXB test sample without reading provider outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path

from harness.web_fetch.webmainbench_runner import load_wcxb_source, selected_wcxb_input

SEED = "wcxb-v1-test-stratified-200-20260917"


class VisibleBody(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_body = False
        self.skip = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'body': self.in_body = True
        if tag in ('script', 'style', 'template', 'noscript', 'svg'): self.skip.append(tag)

    def handle_endtag(self, tag):
        if tag == 'body': self.in_body = False
        if self.skip and self.skip[-1] == tag: self.skip.pop()

    def handle_data(self, data):
        if self.in_body and not self.skip: self.parts.append(data)


def full_test_exclusions(records: list[dict]) -> list[dict]:
    """Source-only validity check; never consult provider outputs or scores."""
    excluded = []
    for record in records:
        reference = record['groundtruth_content']
        visible = VisibleBody()
        visible.feed(record['html'])
        body = ' '.join(' '.join(visible.parts).split())
        if not reference.strip():
            reason = 'empty_main_content_reference'
        elif not body:
            reason = 'reference_body_absent_from_frozen_html'
        else:
            continue
        excluded.append({'file_id': record['track_id'], 'reason': reason,
                         'visible_body_chars': len(body), 'reference_chars': len(reference)})
    return excluded


def validate_manifest(records: list[dict], source_hash: str, manifest: dict) -> tuple[list[dict], dict]:
    scope = manifest.get('scope')
    if scope in ('full_test', 'qualified_test'):
        invalid = {row['file_id'] for row in full_test_exclusions(records)}
        selected = sorted((r for r in records if scope == 'full_test' or r['track_id'] not in invalid), key=lambda r: r['track_id'])
        distribution = {'parent': dict(Counter(r['meta']['page_type'] for r in records)),
                        'sample': dict(Counter(r['meta']['page_type'] for r in selected))}
        if scope == 'full_test':
            expected_exclusions = full_test_exclusions(selected)
            if (manifest.get('size') != len(selected) or manifest.get('exclusions') != expected_exclusions
                    or manifest.get('primary_quality_case_count') != len(selected) - len(expected_exclusions)):
                raise ValueError('WCXB full-test source qualification drifted')
        elif manifest.get('size') != len(selected) or manifest.get('exclusions') or manifest.get('primary_quality_case_count') != len(selected):
            raise ValueError('WCXB qualified-test selection drifted')
    else:
        selected, distribution = choose(records, 200)
        if manifest.get('seed') != SEED or manifest.get('size') != 200:
            raise ValueError('WCXB sample selection drifted')
    expected_cases = [{'file_id': r['track_id'], 'page_type': r['meta']['page_type']} for r in selected]
    actual_cases = manifest.get('cases')
    if manifest.get('schema_version') == 2:
        if manifest.get('source_count') != len(records):
            raise ValueError('WCXB qualified edition count differs from its inputs')
        actual_cases = [{'file_id': c['file_id'], 'page_type': c['page_type']} for c in actual_cases]
        by_id = {r['track_id']: r for r in selected}
        for case in manifest['cases']:
            row = by_id.get(case['file_id'])
            if row is None or any(case.get(field) != value for field, value in (
                    ('url', row['url']), ('reference', row['groundtruth_content']),
                    ('with', row['ground_truth'].get('with') or []),
                    ('without', row['ground_truth'].get('without') or []))):
                raise ValueError('WCXB task fields differ from frozen ground truth')
    if (manifest.get('source_sha256') != source_hash or manifest.get('split') != 'test'
            or manifest.get('distribution') != distribution or actual_cases != expected_cases):
        raise ValueError('WCXB manifest differs from source-only selection')
    return selected, distribution


def choose(records: list[dict], size: int) -> tuple[list[dict], dict]:
    groups = defaultdict(list)
    for record in records:
        groups[record["meta"]["page_type"]].append(record)
    counts = {kind: len(items) for kind, items in sorted(groups.items())}
    ideal = {kind: size * count / len(records) for kind, count in counts.items()}
    quotas = {kind: int(value) for kind, value in ideal.items()}
    remaining = size - sum(quotas.values())
    for kind in sorted(counts, key=lambda k: (-(ideal[k] - quotas[k]), k))[:remaining]:
        quotas[kind] += 1
    selected = []
    for kind in sorted(groups):
        ordered = sorted(groups[kind], key=lambda r: hashlib.sha256(
            f"{SEED}:{r['track_id']}".encode()).hexdigest())
        selected.extend(ordered[:quotas[kind]])
    selected.sort(key=lambda r: r["track_id"])
    return selected, {"parent": counts, "sample": dict(Counter(r["meta"]["page_type"] for r in selected))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--full-test", action="store_true")
    parser.add_argument("--qualified-test", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Sample manifest already exists")
    edition_path = None
    if args.source is None:
        args.source, edition_path = selected_wcxb_input()
    elif (args.source.parent / 'tasks.json').is_file():
        edition_path = args.source.parent / 'tasks.json'
    records, source_hash = load_wcxb_source(args.source, manifest=edition_path)
    edition = json.loads(edition_path.read_bytes()) if edition_path is not None else {}
    if edition.get('schema_version') == 2:
        validate_manifest(records, source_hash, edition)
    if args.full_test and args.qualified_test:
        parser.error('Choose one test scope')
    if args.full_test or args.qualified_test:
        invalid = {row['file_id'] for row in full_test_exclusions(records)} if args.qualified_test else set()
        selected = sorted((r for r in records if r['track_id'] not in invalid), key=lambda r: r['track_id'])
        distribution = {'parent': dict(Counter(r['meta']['page_type'] for r in records)),
                        'sample': dict(Counter(r['meta']['page_type'] for r in selected))}
    else:
        selected, distribution = choose(records, 200)
    payload = {"schema_version": 1, "dataset_id": "wcxb-v1-test-qualified" if args.qualified_test else "wcxb-v1-test-full" if args.full_test else "wcxb-v1-test-stratified-200",
               "source_commit": "c039d5ee9f5a3a984a0e167e63aacd04e76e78a9",
               "source_sha256": source_hash, "split": "test", "scope": "qualified_test" if args.qualified_test else "full_test" if args.full_test else "stratified_sample",
               "seed": None if (args.full_test or args.qualified_test) else SEED,
               "algorithm": "all source-qualified test cases by file ID" if args.qualified_test else "all test cases by file ID" if args.full_test else "Hamilton proportional quotas by page type; within each type sort by SHA-256(seed:case_id)",
               "size": len(selected), "distribution": distribution,
               "cases": [{"file_id": r["track_id"], "page_type": r["meta"]["page_type"]} for r in selected]}
    if edition.get('schema_version') == 2:
        payload.update(schema_version=2, source_count=len(records),
                       dataset_id=edition['dataset_id'],
                       cases=[{'file_id': r['track_id'], 'page_type': r['meta']['page_type'],
                               'url': r['url'], 'reference': r['groundtruth_content'],
                               'with': r['ground_truth'].get('with') or [],
                               'without': r['ground_truth'].get('without') or []} for r in selected])
        if 'source_commit' in edition:
            payload['source_commit'] = edition['source_commit']
        else:
            payload.pop('source_commit')
    if args.full_test:
        exclusions = full_test_exclusions(selected)
        payload['exclusions'] = exclusions
        payload['primary_quality_case_count'] = len(selected) - len(exclusions)
    if args.qualified_test:
        payload['primary_quality_case_count'] = len(selected)
    validate_manifest(records, source_hash, payload)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"source_sha256": source_hash, "sample_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
                      "distribution": distribution}, ensure_ascii=False))


if __name__ == "__main__":
    main()
