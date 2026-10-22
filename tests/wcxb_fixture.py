"""Synthetic frozen WCXB sources and certified calls; never contact a provider."""

import gzip
import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path

from harness.web_fetch.wcxb_dataset import validate_manifest
from harness.web_fetch.webmainbench_runner import load_wcxb_source

EVALUATOR = """import json
import shutil
from copy import deepcopy
from pathlib import Path

def load_ground_truth(split):
    root = Path(__file__).parent / split / "ground-truth"
    return {path.stem: json.loads(path.read_text())["ground_truth"] for path in root.glob("*.json")}

def word_f1(predicted, reference):
    left, right = set(predicted.split()), set(reference.split())
    overlap = len(left & right)
    precision = overlap / len(left) if left else 0.0
    recall = overlap / len(right) if right else 0.0
    return precision, recall, 2 * precision * recall / (precision + recall) if overlap else 0.0

def snippet_check(text, snippets):
    return sum(value in text for value in snippets) / len(snippets) if snippets else 1.0
"""


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join((json.dumps(row) + "\n").encode() for row in rows))


def create_cohort(root: Path, *, paired=False, providers=("alpha", "beta", "gamma")):
    source, evidence = root / "source", root / "evidence"
    (source / "test/html").mkdir(parents=True)
    (source / "evaluate.py").write_text(EVALUATOR)
    for index in range(511):
        case_id = f"case-{index:03d}"
        text = f"Independent article {index}" if index < 2 else ""
        write_json(
            source / f"test/ground-truth/{case_id}.json",
            {
                "url": f"https://example.invalid/{case_id}",
                "ground_truth": {
                    "page_type": "article",
                    "main_content": text,
                    "with": ["Independent"],
                    "without": ["Forbidden"],
                },
                "_internal": {"page_type": {"primary": "article"}},
            },
        )
        (source / f"test/html/{case_id}.html.gz").write_bytes(
            gzip.compress(f"<html><body><main>{text}</main></body></html>".encode(), mtime=0)
        )
    records, source_hash = load_wcxb_source(source)
    manifest = {
        "dataset_id": "synthetic-wcxb",
        "source_sha256": source_hash,
        "split": "test",
        "scope": "qualified_test",
        "size": 2,
        "primary_quality_case_count": 2,
        "distribution": {"parent": {"article": 511}, "sample": {"article": 2}},
        "cases": [{"file_id": f"case-{i:03d}", "page_type": "article"} for i in range(2)],
    }
    selected, _ = validate_manifest(records, source_hash, manifest)
    manifest_path = root / "manifest.json"
    write_json(manifest_path, manifest)
    sample_hash = sha(manifest_path.read_bytes())
    profile = {
        "comparison_kind": "wcxb_frozen_html_to_markdown",
        "providers": list(providers),
        "source_dataset_sha256": source_hash,
        "sample_manifest_sha256": sample_hash,
        "official_evaluator_sha256": sha((source / "evaluate.py").read_bytes()),
        "case_count": 2,
        "primary_quality_case_count": 2,
        "max_failure_retries": 1,
        "timeout_seconds": 45,
        "runs_per_case": 1,
    }
    runs = []
    logical_by_provider = {}
    for provider in providers:
        run_dir = evidence / provider
        physical, logical = [], []
        run_id = "run-" + provider
        identity = {"binary_sha256": sha(provider.encode())}
        for record in selected:
            cid = record["track_id"]
            failed = provider == "beta" and cid == "case-000"
            content = "" if failed else record["groundtruth_content"]
            attempts = []
            for number in range(1, 3 if failed else 2):
                relative = f"artifacts/{cid}-p{number:02d}.txt"
                path = run_dir / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                call = {
                    "provider": provider,
                    "run_id": run_id,
                    "track_id": cid,
                    "attempt_id": f"{provider}-{cid}-p{number}",
                    "physical_attempt": number,
                    "elapsed_ms": float(number),
                    "ok": not failed,
                    "status_code": None if failed else 200,
                    "error_type": "TimeoutError" if failed else None,
                    "error_message": "request timeout" if failed else None,
                    "input_html_sha256": sha(record["html"].encode()),
                    "reference_sha256": sha(record["groundtruth_content"].encode()),
                    "output_sha256": sha(content.encode()),
                    "output_chars": len(content),
                    "physical_artifact_path": relative,
                }
                physical.append(call)
                attempts.append(call)
            final = {
                key: value
                for key, value in attempts[-1].items()
                if key not in ("attempt_id", "physical_attempt")
            }
            final.update(
                attempt_id=f"{provider}-{cid}-r001",
                artifact_path=f"artifacts/{cid}.md",
                physical_attempt_count=len(attempts),
                total_elapsed_ms=sum(row["elapsed_ms"] for row in attempts),
                retry_selection="final_physical_attempt_is_authoritative",
            )
            (run_dir / final["artifact_path"]).write_text(content)
            logical.append(final)
        write_jsonl(run_dir / "results.jsonl", logical)
        write_jsonl(run_dir / "physical-attempts.jsonl", physical)
        write_json(
            run_dir / "execution.json",
            {
                "provider": provider,
                "run_id": run_id,
                "implementation_identity": identity,
                "case_count": 2,
                "max_failure_retries": 1,
                "timeout_seconds": 45,
                "source_dataset_sha256": source_hash,
                "selection_manifest": {"sha256": sample_hash},
            },
        )
        runs.append(
            {
                "provider": provider,
                "run_id": run_id,
                "identity": identity,
                "results_sha256": sha((run_dir / "results.jsonl").read_bytes()),
            }
        )
        logical_by_provider[provider] = logical
    certificate = {
        "benchmark": manifest["dataset_id"],
        "complete": True,
        "providers": runs,
        "source_sha256_before": source_hash,
        "source_sha256_after": source_hash,
        "sample_sha256_before": sample_hash,
        "sample_sha256_after": sample_hash,
    }
    write_json(evidence / "certification.json", certificate)
    if paired:
        roles = {"release": providers[0], "pr": providers[-1]}
        profile.update(
            release_binary_sha256=runs[0]["identity"]["binary_sha256"],
            candidate_binary_sha256=runs[-1]["identity"]["binary_sha256"],
        )
        calls = []
        for round_number in (0, 1):
            for index, record in enumerate(selected):
                first = "release" if (index + round_number) % 2 == 0 else "pr"
                for order, method in enumerate(
                    (first, "pr" if first == "release" else "release"), 1
                ):
                    result = logical_by_provider[roles[method]][index]
                    calls.append(
                        {
                            "case": record["track_id"],
                            "round": round_number,
                            "method": method,
                            "order": order,
                            "ok": True,
                            "error_type": None,
                            "elapsed_ms": 11.0 if method == "release" else 13.0,
                            "output_sha256": result["output_sha256"],
                        }
                    )
        correction = evidence / "latency-correction"
        write_jsonl(correction / "calls.jsonl", calls)
        receipt = {
            "kind": "wcxb_full_paired_latency",
            "case_count": 2,
            "rounds": 2,
            "selection": "final round, all cases",
            "call_count": 8,
            "source_sha256": source_hash,
            "manifest_sha256": sample_hash,
            "binary_sha256": {method: sha(name.encode()) for method, name in roles.items()},
            "calls_sha256": sha((correction / "calls.jsonl").read_bytes()),
            "host_load_1m_checks": [1.0] * 50,
            "quiet_host_load_limit": 4.0,
        }
        write_json(correction / "receipt.json", receipt)
        receipt_hash = sha((correction / "receipt.json").read_bytes())
        profile["latency_correction_receipt_sha256"] = receipt_hash
        write_json(
            correction / "certification.json",
            {
                "kind": "certified_wcxb_paired_latency",
                "complete": True,
                "case_count": 2,
                "physical_call_count": 8,
                "selected_round": 1,
                "source_sha256_before": source_hash,
                "source_sha256_after": source_hash,
                "sample_sha256_before": sample_hash,
                "sample_sha256_after": sample_hash,
                "calls_sha256": receipt["calls_sha256"],
                "receipt_sha256": receipt_hash,
            },
        )
    profile_path = root / "profile.json"
    write_json(profile_path, profile)
    return dict(
        source=source,
        sample_manifest=manifest_path,
        evidence_root=evidence,
        profile_path=profile_path,
    )


PROVIDERS = ('moli', 'kitesurf', 'lightpanda', 'obscura', 'chromium', 'moli-pr643')
CASES = ('4017', '4461', '5649', '5677')


def create_report_inputs(tmp_path, *, english_source=True):
    from harness.record_io import load_jsonl
    from harness.web_fetch.wcxb_scoring import score

    cohort = create_cohort(tmp_path / 'cohort', paired=True, providers=PROVIDERS)
    summary = score(**cohort)
    original = load_jsonl(cohort['evidence_root'] / 'wcxb-scores.jsonl')
    profile = json.loads(cohort['profile_path'].read_bytes())
    profile.update(source_repository='https://example.invalid/synthetic', source_commit='a' * 40,
                   candidate_source_commit='b' * 40,
                   versions={provider: 'synthetic' for provider in PROVIDERS})
    root = tmp_path / 'report'
    root.mkdir()
    write_json(root / 'research/wcxb-representative-review.json',
               {'case_count': 0, 'result_count': 0, 'cases': []})
    shutil.copy2(Path(__file__).resolve().parents[1] / 'research/provider-adoption.json',
                 root / 'research/provider-adoption.json')
    (root / 'content').mkdir()
    (root / 'content/wcxb-repair-zh.fragment').write_text('<section id="guide-repair">合成样例')
    if english_source:
        (root / 'content/wcxb-repair-en.fragment').write_text('<section id="guide-repair">Synthetic specimen')
    rows, ground_truth, calls = [], {}, []
    for case in CASES:
        ground_truth[case] = {'main_content': 'Independent article 0', 'page_type': 'article',
                              'with': ['Independent'], 'without': ['Forbidden']}
        target = root / f'inputs/{case}.txt'
        target.parent.mkdir(exist_ok=True)
        target.write_text('<main>Independent article 0</main>')
        for provider in PROVIDERS:
            row = deepcopy(next(row for row in original
                                if row['provider'] == provider and row['case_id'] == 'case-000'))
            row.update(case_id=case, attempt_id=f'{provider}-{case}',
                       original_url=f'https://example.invalid/{case}')
            rows.append(row)
            artifact = root / f'artifacts/{provider}/{case}.txt'
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text('Independent article 0')
    for round_number in (0, 1):
        for index, case in enumerate(CASES):
            methods = ('release', 'pr') if (index + round_number) % 2 == 0 else ('pr', 'release')
            calls.extend({'case': case, 'round': round_number, 'method': method, 'order': order,
                          'ok': True, 'error_type': None, 'elapsed_ms': 11 if method == 'release' else 13,
                          'output_sha256': sha(b'Independent article 0')}
                         for order, method in enumerate(methods, 1))
    timing = root / 'research/wcxb-full-paired-latency'
    write_jsonl(timing / 'calls.jsonl', calls)
    receipt = json.loads((cohort['evidence_root'] / 'latency-correction/receipt.json').read_bytes())
    receipt.update(case_count=len(CASES), call_count=len(calls),
                   calls_sha256=sha((timing / 'calls.jsonl').read_bytes()))
    write_json(timing / 'receipt.json', receipt)
    profile['latency_correction_receipt_sha256'] = sha((timing / 'receipt.json').read_bytes())
    summary.update(case_count=len(CASES), quality_case_count=len(CASES))
    identities = {provider: {'reported_version': 'synthetic', 'binary_sha256': sha(provider.encode())} for provider in PROVIDERS}
    content = {"schema_version": 1, "guide": {"zh": "<section><h1>合成报告解读</h1></section>"}}
    if english_source:
        content["guide"]["en"] = "<section><h1>Synthetic interpretation</h1></section>"
    return root, rows, summary, ground_truth, identities, profile, content
